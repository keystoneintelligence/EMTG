// Focused tests for EMTG's Ipopt adapter. These use a tiny analytic NLP so
// callback, scaling, sparsity, cache, status, and failure behavior are tested
// without mission-model or SPICE noise.

#include "IPOPT_interface.h"
#include "NLPInterfaceFactory.h"

#include <cmath>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

namespace
{
    void require(const bool condition, const std::string& message)
    {
        if (!condition)
            throw std::runtime_error(message);
    }

    void require_close(const double actual, const double expected, const double tolerance, const std::string& message)
    {
        if (!std::isfinite(actual) || std::fabs(actual - expected) > tolerance)
            throw std::runtime_error(message + ": expected " + std::to_string(expected) + ", got " + std::to_string(actual));
    }

    template<typename Callable>
    void require_throws(Callable&& callable, const std::string& expectedText)
    {
        try
        {
            callable();
        }
        catch (const std::exception& error)
        {
            require(std::string(error.what()).find(expectedText) != std::string::npos,
                "exception did not contain '" + expectedText + "': " + error.what());
            return;
        }
        throw std::runtime_error("expected exception containing '" + expectedText + "'");
    }

    class AnalyticProblem : public EMTG::problem
    {
    public:
        explicit AnalyticProblem(const bool constrained = true) :
            constrained(constrained),
            returnNaN(false),
            evaluationCount(0),
            derivativeEvaluationCount(0),
            cacheUpgradeCount(0),
            redundantEvaluationCount(0),
            haveLastEvaluation(false),
            lastEvaluationNeededDerivatives(false),
            lastUnscaledObjective(0.0)
        {
            this->options.enable_Scalatron = false;
            this->options.NLP_solver_type = 2;
            this->options.quiet_NLP = true;
            this->options.snopt_feasibility_tolerance = 1.0e-8;
            this->options.working_directory = ".";
            this->options.mission_name = "ipopt_analytic_test";

            if (constrained)
            {
                this->total_number_of_NLP_parameters = 2;
                this->total_number_of_constraints = 2;
                this->Xlowerbounds = { 10.0, -3.0 };
                this->Xupperbounds = { 14.0, 5.0 };
                this->X_scale_factors = { 2.0, 4.0 };
                this->Xdescriptions = { "x", "y" };
                this->Flowerbounds = { -1.0e+100, 14.0 };
                this->Fupperbounds = { 1.0e+100, 14.0 };
                this->F_scale_factors = { 1.0, 1.0 };
                this->Fdescriptions = { "objective", "x + y" };

                // The constraint derivative for x is deliberately split across
                // duplicate linear entries and an overlapping nonlinear entry.
                this->A = { 0.75, 0.25 };
                this->iAfun = { 1, 1 };
                this->jAvar = { 0, 0 };
                this->Adescriptions = { "linear x part 1", "linear x part 2" };

                // Objective x is duplicated; constraint y is duplicated. The
                // adapter must aggregate every equal (row, column) location.
                this->G.assign(6, 0.0);
                this->iGfun = { 0, 0, 0, 1, 1, 1 };
                this->jGvar = { 0, 0, 1, 0, 1, 1 };
                this->Gdescriptions = {
                    "objective x part 1",
                    "objective x part 2",
                    "objective y",
                    "constraint x nonlinear part",
                    "constraint y part 1",
                    "constraint y part 2"
                };
            }
            else
            {
                this->total_number_of_NLP_parameters = 1;
                this->total_number_of_constraints = 1;
                this->Xlowerbounds = { 0.0 };
                this->Xupperbounds = { 10.0 };
                this->X_scale_factors = { 5.0 };
                this->Xdescriptions = { "x" };
                this->Flowerbounds = { -1.0e+100 };
                this->Fupperbounds = { 1.0e+100 };
                this->F_scale_factors = { 1.0 };
                this->Fdescriptions = { "objective" };
                this->G.assign(1, 0.0);
                this->iGfun = { 0 };
                this->jGvar = { 0 };
                this->Gdescriptions = { "objective x" };
            }

            this->F.assign(this->total_number_of_constraints, 0.0);
            this->X.assign(this->total_number_of_NLP_parameters, 0.0);
            this->Xopt.assign(this->total_number_of_NLP_parameters, 0.0);
            this->locate_equality_constraints();
        }

        void calcbounds() override {}

        std::vector<double> construct_initial_guess() override
        {
            return this->constrained ? std::vector<double>{ 12.0, 2.0 } : std::vector<double>{ 8.0 };
        }

        void evaluate(const std::vector<doubleType>& X,
            std::vector<doubleType>& F,
            std::vector<double>& G,
            const bool& needG) override
        {
            ++this->evaluationCount;
            if (needG)
                ++this->derivativeEvaluationCount;

            bool samePoint = this->haveLastEvaluation && this->lastEvaluationX.size() == X.size();
            for (size_t index = 0; samePoint && index < X.size(); ++index)
                samePoint = this->lastEvaluationX[index] == X[index] _GETVALUE;

            if (samePoint)
            {
                if (!this->lastEvaluationNeededDerivatives && needG)
                    ++this->cacheUpgradeCount;
                else
                    ++this->redundantEvaluationCount;
            }

            this->lastEvaluationX.resize(X.size());
            for (size_t index = 0; index < X.size(); ++index)
                this->lastEvaluationX[index] = X[index] _GETVALUE;
            this->lastEvaluationNeededDerivatives = needG;
            this->haveLastEvaluation = true;

            if (this->returnNaN)
            {
                F[0] = std::numeric_limits<double>::quiet_NaN();
                return;
            }

            if (this->constrained)
            {
                const double x = X[0] _GETVALUE;
                const double y = X[1] _GETVALUE;
                const double objective = (x - 13.0) * (x - 13.0) + (y - 2.0) * (y - 2.0);
                F[0] = objective;
                F[1] = x + y;
                this->lastUnscaledObjective = objective;

                if (needG)
                {
                    // Derivatives are with respect to EMTG's scaled variables.
                    G[0] = 2.0 * (x - 13.0);
                    G[1] = 2.0 * (x - 13.0);
                    G[2] = 8.0 * (y - 2.0);
                    G[3] = 1.0;
                    G[4] = 1.5;
                    G[5] = 2.5;
                }
            }
            else
            {
                const double x = X[0] _GETVALUE;
                const double objective = (x - 3.0) * (x - 3.0);
                F[0] = objective;
                this->lastUnscaledObjective = objective;
                if (needG)
                    G[0] = 10.0 * (x - 3.0);
            }
        }

        void output(const std::string&) override {}

        doubleType getUnscaledObjective() override
        {
            return this->lastUnscaledObjective;
        }

        bool constrained;
        bool returnNaN;
        size_t evaluationCount;
        size_t derivativeEvaluationCount;
        size_t cacheUpgradeCount;
        size_t redundantEvaluationCount;

    private:
        bool haveLastEvaluation;
        bool lastEvaluationNeededDerivatives;
        std::vector<double> lastEvaluationX;
        double lastUnscaledObjective;
    };

    EMTG::Solvers::NLPoptions default_options()
    {
        EMTG::Solvers::NLPoptions options;
        options.set_quiet_NLP(true);
        options.set_major_iterations_limit(100);
        options.set_max_run_time_seconds(30);
        options.set_feasibility_tolerance(1.0e-8);
        options.set_optimality_tolerance(1.0e-9);
        return options;
    }

    void test_status_mapping()
    {
        using namespace EMTG::Solvers;
        require(MapIPOPTStatus(Solve_Succeeded, false, false, false).status == NLPSolveStatus::Converged,
            "Solve_Succeeded mapping");
        require(MapIPOPTStatus(Solved_To_Acceptable_Level, false, false, false).status == NLPSolveStatus::Acceptable,
            "acceptable mapping");
        require(MapIPOPTStatus(Maximum_Iterations_Exceeded, false, false, false).status == NLPSolveStatus::MaxIterations,
            "iteration-limit mapping");
        require(MapIPOPTStatus(Maximum_WallTime_Exceeded, false, false, false).status == NLPSolveStatus::MaxTime,
            "wall-time mapping");
        require(MapIPOPTStatus(User_Requested_Stop, true, false, false).solverAcceptedPoint,
            "goal stop should be acceptable pending EMTG feasibility");
        require(MapIPOPTStatus(User_Requested_Stop, false, true, false).status == NLPSolveStatus::MaxTime,
            "callback time-limit mapping");
        require(MapIPOPTStatus(User_Requested_Stop, false, false, true).status == NLPSolveStatus::Failed,
            "callback failure mapping");
        require(MapIPOPTStatus(Invalid_Number_Detected, false, false, false).status == NLPSolveStatus::Failed,
            "invalid-number mapping");
    }

    void test_factory_selection_and_errors()
    {
        using namespace EMTG::Solvers;
        require(IsNLPSolverAvailable(2), "IPOPT should be available in the IPOPT test build");
        require(!IsNLPSolverAvailable(1), "WORHP must never be advertised");

        AnalyticProblem problem;
        NLPoptions options = default_options();
        problem.options.NLP_solver_type = 0;
        require_throws([&]() { CreateNLPInterface(&problem, options); }, "does not include SNOPT");

        problem.options.NLP_solver_type = 1;
        require_throws([&]() { CreateNLPInterface(&problem, options); }, "WORHP is deprecated and unsupported");

        problem.options.NLP_solver_type = 2;
        std::unique_ptr<NLP_interface> solver = CreateNLPInterface(&problem, options);
        require(solver->getSolverName() == "IPOPT", "factory did not select IPOPT");
    }

    void test_scaled_constrained_solve_and_cache()
    {
        AnalyticProblem problem;
        EMTG::Solvers::NLPoptions options = default_options();
        EMTG::Solvers::IPOPT_interface solver(&problem, options);
        solver.setX0_unscaled({ 11.0, 3.0 });
        solver.run_NLP(false);

        const std::vector<doubleType> solution = solver.getX_unscaled();
        require_close(solution[0] _GETVALUE, 12.5, 2.0e-5, "scaled constrained x");
        require_close(solution[1] _GETVALUE, 1.5, 2.0e-5, "scaled constrained y");
        require_close(solver.getF()[0] _GETVALUE, 0.5, 2.0e-5, "scaled constrained objective");
        require_close(solver.getF()[1] _GETVALUE, 14.0, 1.0e-7, "scaled equality constraint");
        require(solver.getLastSolveWasAcceptable(), "constrained solve should be acceptable to EMTG");
        require(problem.derivativeEvaluationCount > 0, "derivative callback was not exercised");
        require(problem.cacheUpgradeCount > 0, "value-to-derivative cache upgrade was not exercised");
        require(problem.redundantEvaluationCount == 0, "cache allowed a redundant identical evaluation");
    }

    void test_unconstrained_zero_constraint_solve()
    {
        AnalyticProblem problem(false);
        EMTG::Solvers::NLPoptions options = default_options();
        EMTG::Solvers::IPOPT_interface solver(&problem, options);
        solver.setX0_unscaled({ 9.0 });
        solver.run_NLP(false);

        require_close(solver.getX_unscaled()[0] _GETVALUE, 3.0, 2.0e-5, "unconstrained solution");
        require_close(solver.getF()[0] _GETVALUE, 0.0, 1.0e-8, "unconstrained objective");
        require(solver.getLastSolveWasAcceptable(), "unconstrained solve should be acceptable");
    }

    void test_iteration_limit_status()
    {
        AnalyticProblem problem;
        EMTG::Solvers::NLPoptions options = default_options();
        options.set_major_iterations_limit(0);
        EMTG::Solvers::IPOPT_interface solver(&problem, options);
        solver.setX0_unscaled({ 11.0, 3.0 });
        solver.run_NLP(false);
        require(solver.getLastSolveStatus() == EMTG::Solvers::NLPSolveStatus::MaxIterations,
            "max_iter=0 did not map to MaxIterations");
        require(!solver.getLastSolveWasAcceptable(), "iteration-limit exit was incorrectly accepted");
    }

    void test_goal_stop_without_chaperone()
    {
        AnalyticProblem problem;
        EMTG::Solvers::NLPoptions options = default_options();
        options.set_enable_NLP_chaperone(false);
        options.set_stop_on_goal_attain(true);
        options.set_objective_goal(0.75);
        EMTG::Solvers::IPOPT_interface solver(&problem, options);
        solver.setX0_unscaled({ 12.5, 1.5 });
        solver.run_NLP(false);
        require(solver.getLastSolveStatus() == EMTG::Solvers::NLPSolveStatus::UserStopped,
            "goal stop did not run when the chaperone was disabled");
        require(solver.getLastSolveWasAcceptable(), "feasible goal stop should be acceptable");
    }

    void test_invalid_inputs_and_nan_failure()
    {
        EMTG::Solvers::NLPoptions options = default_options();

        AnalyticProblem invalidScale;
        invalidScale.X_scale_factors[0] = 0.0;
        require_throws([&]() { EMTG::Solvers::IPOPT_interface solver(&invalidScale, options); }, "scale factor");

        AnalyticProblem nanProblem;
        nanProblem.returnNaN = true;
        EMTG::Solvers::IPOPT_interface solver(&nanProblem, options);
        solver.setX0_unscaled({ 12.5, 1.5 });
        require_throws([&]() { solver.run_NLP(false); }, "contains NaN or infinity");
    }
}

int main()
{
    try
    {
        test_status_mapping();
        test_factory_selection_and_errors();
        test_scaled_constrained_solve_and_cache();
        test_unconstrained_zero_constraint_solve();
        test_iteration_limit_status();
        test_goal_stop_without_chaperone();
        test_invalid_inputs_and_nan_failure();
        std::cout << "IPOPT interface tests passed" << std::endl;
        return 0;
    }
    catch (const std::exception& error)
    {
        std::cerr << "IPOPT interface test failure: " << error.what() << std::endl;
        return 1;
    }
}
