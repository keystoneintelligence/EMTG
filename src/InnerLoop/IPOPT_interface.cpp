// EMTG: Evolutionary Mission Trajectory Generator
// An open-source global optimization tool for preliminary mission design
// Provided by NASA Goddard Space Flight Center
//
// Copyright (c) 2013 - 2024 United States Government as represented by the
// Administrator of the National Aeronautics and Space Administration.
// All Other Rights Reserved.

#include "IPOPT_interface.h"

#include "EMTG_math.h"

#include <algorithm>
#include <cmath>
#include <ctime>
#include <iostream>
#include <limits>
#include <map>
#include <sstream>
#include <stdexcept>
#include <utility>

namespace EMTG
{
    namespace Solvers
    {
        namespace
        {
            char* mutable_string(const char* text)
            {
                return const_cast<char*>(text);
            }
        }//end anonymous namespace

        IPOPTStatusMapping MapIPOPTStatus(const ApplicationReturnStatus status,
            const bool stoppedOnGoalAttain,
            const bool stoppedOnTimeLimit,
            const bool callbackFailed)
        {
            switch (status)
            {
            case Solve_Succeeded:
                return { NLPSolveStatus::Converged, true };
            case Solved_To_Acceptable_Level:
            case Search_Direction_Becomes_Too_Small:
                return { NLPSolveStatus::Acceptable, true };
            case Feasible_Point_Found:
                return { NLPSolveStatus::FeasiblePoint, true };
            case Maximum_Iterations_Exceeded:
                return { NLPSolveStatus::MaxIterations, false };
            case Maximum_CpuTime_Exceeded:
            case Maximum_WallTime_Exceeded:
                return { NLPSolveStatus::MaxTime, false };
            case User_Requested_Stop:
                if (callbackFailed)
                    return { NLPSolveStatus::Failed, false };
                if (stoppedOnTimeLimit)
                    return { NLPSolveStatus::MaxTime, false };
                return { NLPSolveStatus::UserStopped, stoppedOnGoalAttain };
            default:
                return { NLPSolveStatus::Failed, false };
            }
        }

        IPOPT_interface::IPOPT_interface(problem* myProblem,
            const NLPoptions& myOptions) :
            NLP_interface::NLP_interface(myProblem, myOptions),
            myIPOPT(nullptr),
            stoppedOnGoalAttain(false),
            stoppedOnTimeLimit(false),
            callbackFailed(false)
        {
            this->initialize_problem();
        }

        IPOPT_interface::~IPOPT_interface()
        {
            if (this->myIPOPT)
            {
                FreeIpoptProblem(this->myIPOPT);
                this->myIPOPT = nullptr;
            }
        }

        void IPOPT_interface::initialize_problem()
        {
            this->reset_solver_state("IPOPT");

            if (this->nX == 0)
                throw std::invalid_argument("IPOPT requires at least one decision variable.");
            if (this->nF == 0)
                throw std::invalid_argument("IPOPT requires an objective row in the EMTG function vector.");
            if (this->nX > static_cast<size_t>(std::numeric_limits<ipindex>::max())
                || this->nF - 1 > static_cast<size_t>(std::numeric_limits<ipindex>::max()))
                throw std::overflow_error("The EMTG NLP dimensions exceed Ipopt's configured index width.");

            if (this->myProblem->Xlowerbounds.size() != this->nX
                || this->myProblem->Xupperbounds.size() != this->nX
                || this->myProblem->X_scale_factors.size() != this->nX)
                throw std::invalid_argument("IPOPT decision-variable bounds and scale factors must match the NLP dimension.");
            if (this->Flowerbounds.size() != this->nF || this->Fupperbounds.size() != this->nF)
                throw std::invalid_argument("IPOPT function bounds must include exactly one objective row plus all constraint rows.");

            this->ipopt_variable_lower_bounds.resize(this->nX, 0.0);
            this->ipopt_variable_upper_bounds.resize(this->nX, 0.0);
            this->ipopt_x.resize(this->nX, 0.0);
            this->ipopt_bound_multipliers_lower.resize(this->nX, 0.0);
            this->ipopt_bound_multipliers_upper.resize(this->nX, 0.0);
            this->ipopt_objective_gradient.resize(this->nX, 0.0);

            for (size_t Xindex = 0; Xindex < this->nX; ++Xindex)
            {
                const double scale = this->myProblem->X_scale_factors[Xindex];
                if (!std::isfinite(scale) || scale <= 0.0)
                    throw std::invalid_argument("IPOPT requires every decision-variable scale factor to be finite and positive.");
                if (!std::isfinite(this->myProblem->Xlowerbounds[Xindex])
                    || !std::isfinite(this->myProblem->Xupperbounds[Xindex])
                    || this->myProblem->Xlowerbounds[Xindex] > this->myProblem->Xupperbounds[Xindex])
                    throw std::invalid_argument("IPOPT received invalid decision-variable bounds.");
                this->Xlowerbounds[Xindex] = 0.0;
                this->Xupperbounds[Xindex] = (this->myProblem->Xupperbounds[Xindex] - this->myProblem->Xlowerbounds[Xindex]) / scale;
                this->ipopt_variable_lower_bounds[Xindex] = this->Xlowerbounds[Xindex];
                this->ipopt_variable_upper_bounds[Xindex] = this->Xupperbounds[Xindex];
            }

            if (this->nF > 1)
            {
                this->ipopt_constraints.resize(this->nF - 1, 0.0);
                this->ipopt_constraint_multipliers.resize(this->nF - 1, 0.0);
                this->ipopt_constraint_lower_bounds.resize(this->nF - 1, 0.0);
                this->ipopt_constraint_upper_bounds.resize(this->nF - 1, 0.0);

                for (size_t Findex = 1; Findex < this->nF; ++Findex)
                {
                    if (!std::isfinite(this->Flowerbounds[Findex])
                        || !std::isfinite(this->Fupperbounds[Findex])
                        || this->Flowerbounds[Findex] > this->Fupperbounds[Findex])
                        throw std::invalid_argument("IPOPT received invalid constraint bounds.");
                    this->ipopt_constraint_lower_bounds[Findex - 1] = this->Flowerbounds[Findex];
                    this->ipopt_constraint_upper_bounds[Findex - 1] = this->Fupperbounds[Findex];
                }
            }

            this->jacobian_iRow.clear();
            this->jacobian_jCol.clear();
            this->jacobianEntries.clear();

            if (this->A.size() != this->nA || this->iAfun.size() != this->nA || this->jAvar.size() != this->nA)
                throw std::invalid_argument("IPOPT linear Jacobian values and sparsity indices have inconsistent sizes.");
            if (this->G.size() != this->nG || this->iGfun.size() != this->nG || this->jGvar.size() != this->nG)
                throw std::invalid_argument("IPOPT nonlinear Jacobian values and sparsity indices have inconsistent sizes.");

            std::map<std::pair<ipindex, ipindex>, size_t> jacobianEntryLookup;
            const auto findOrCreateEntry = [this, &jacobianEntryLookup](const size_t Findex, const size_t Xindex) -> size_t
            {
                if (Findex >= this->nF || Xindex >= this->nX)
                    throw std::out_of_range("IPOPT Jacobian sparsity contains an out-of-range row or column.");

                const std::pair<ipindex, ipindex> location(
                    static_cast<ipindex>(Findex - 1),
                    static_cast<ipindex>(Xindex));
                const auto existing = jacobianEntryLookup.find(location);
                if (existing != jacobianEntryLookup.end())
                    return existing->second;

                const size_t entryIndex = this->jacobianEntries.size();
                jacobianEntryLookup[location] = entryIndex;
                this->jacobian_iRow.push_back(location.first);
                this->jacobian_jCol.push_back(location.second);
                this->jacobianEntries.push_back(JacobianEntry());
                return entryIndex;
            };

            for (size_t Aindex = 0; Aindex < this->nA; ++Aindex)
            {
                if (!std::isfinite(this->A[Aindex]))
                    throw std::invalid_argument("IPOPT received a non-finite linear Jacobian value.");
                if (this->iAfun[Aindex] >= this->nF || this->jAvar[Aindex] >= this->nX)
                    throw std::out_of_range("IPOPT linear Jacobian sparsity contains an out-of-range row or column.");
                if (this->iAfun[Aindex] > 0)
                    this->jacobianEntries[findOrCreateEntry(this->iAfun[Aindex], this->jAvar[Aindex])]
                        .linearSourceIndices.push_back(Aindex);
            }

            for (size_t Gindex = 0; Gindex < this->nG; ++Gindex)
            {
                if (this->iGfun[Gindex] >= this->nF || this->jGvar[Gindex] >= this->nX)
                    throw std::out_of_range("IPOPT nonlinear Jacobian sparsity contains an out-of-range row or column.");
                if (this->iGfun[Gindex] > 0)
                    this->jacobianEntries[findOrCreateEntry(this->iGfun[Gindex], this->jGvar[Gindex])]
                        .nonlinearSourceIndices.push_back(Gindex);
            }

            if (this->jacobianEntries.size() > static_cast<size_t>(std::numeric_limits<ipindex>::max()))
                throw std::overflow_error("The EMTG Jacobian has more entries than Ipopt's configured index width supports.");

            this->myIPOPT = CreateIpoptProblem(static_cast<ipindex>(this->nX),
                this->ipopt_variable_lower_bounds.empty() ? nullptr : this->ipopt_variable_lower_bounds.data(),
                this->ipopt_variable_upper_bounds.empty() ? nullptr : this->ipopt_variable_upper_bounds.data(),
                static_cast<ipindex>(this->nF > 0 ? this->nF - 1 : 0),
                this->ipopt_constraint_lower_bounds.empty() ? nullptr : this->ipopt_constraint_lower_bounds.data(),
                this->ipopt_constraint_upper_bounds.empty() ? nullptr : this->ipopt_constraint_upper_bounds.data(),
                static_cast<ipindex>(this->jacobianEntries.size()),
                0,
                0,
                IPOPT_interface::evaluate_objective,
                IPOPT_interface::evaluate_constraints,
                IPOPT_interface::evaluate_objective_gradient,
                IPOPT_interface::evaluate_jacobian,
                IPOPT_interface::evaluate_hessian);

            if (!this->myIPOPT)
            {
                throw std::runtime_error("CreateIpoptProblem returned null.");
            }

            try
            {
                const auto requireOption = [](const bool success, const char* optionName)
                {
                    if (!success)
                        throw std::runtime_error(std::string("IPOPT rejected option '") + optionName + "'.");
                };

                if (this->myOptions.get_iteration_limit() > static_cast<size_t>(std::numeric_limits<ipindex>::max()))
                    throw std::overflow_error("The requested IPOPT iteration limit exceeds Ipopt's configured index width.");

                requireOption(AddIpoptStrOption(this->myIPOPT, mutable_string("hessian_approximation"), mutable_string("limited-memory")), "hessian_approximation");
                requireOption(AddIpoptStrOption(this->myIPOPT, mutable_string("nlp_scaling_method"), mutable_string("none")), "nlp_scaling_method");
                requireOption(AddIpoptIntOption(this->myIPOPT, mutable_string("max_iter"), static_cast<ipindex>(this->myOptions.get_iteration_limit())), "max_iter");
                requireOption(AddIpoptNumOption(this->myIPOPT, mutable_string("tol"), this->myOptions.get_optimality_tolerance()), "tol");
                requireOption(AddIpoptNumOption(this->myIPOPT, mutable_string("constr_viol_tol"), this->myOptions.get_feasibility_tolerance()), "constr_viol_tol");
                requireOption(AddIpoptNumOption(this->myIPOPT, mutable_string("acceptable_tol"), this->myOptions.get_optimality_tolerance()), "acceptable_tol");
                requireOption(AddIpoptNumOption(this->myIPOPT, mutable_string("acceptable_constr_viol_tol"), this->myOptions.get_feasibility_tolerance()), "acceptable_constr_viol_tol");
                requireOption(AddIpoptNumOption(this->myIPOPT, mutable_string("max_wall_time"), static_cast<ipnumber>(this->myOptions.get_max_run_time_seconds())), "max_wall_time");
                requireOption(AddIpoptNumOption(this->myIPOPT, mutable_string("bound_relax_factor"), 0.0), "bound_relax_factor");

                if (this->myOptions.get_check_derivatives())
                    requireOption(AddIpoptStrOption(this->myIPOPT, mutable_string("derivative_test"), mutable_string("first-order")), "derivative_test");

                if (this->myOptions.get_quiet_NLP())
                {
                    requireOption(AddIpoptIntOption(this->myIPOPT, mutable_string("print_level"), 0), "print_level");
                    requireOption(AddIpoptStrOption(this->myIPOPT, mutable_string("sb"), mutable_string("yes")), "sb");
                }
                else
                    requireOption(AddIpoptIntOption(this->myIPOPT, mutable_string("print_level"), 5), "print_level");

                const std::string output_file_path = this->myOptions.get_output_file_path();
                if (!output_file_path.empty())
                    requireOption(OpenIpoptOutputFile(this->myIPOPT, mutable_string(output_file_path.c_str()), this->myOptions.get_quiet_NLP() ? 0 : 5), "output_file");

                requireOption(SetIntermediateCallback(this->myIPOPT, IPOPT_interface::intermediate_callback), "intermediate_callback");
            }
            catch (...)
            {
                FreeIpoptProblem(this->myIPOPT);
                this->myIPOPT = nullptr;
                throw;
            }
        }

        void IPOPT_interface::run_NLP(const bool& X0_is_scaled)
        {
            this->reset_solver_state("IPOPT");
            this->stoppedOnGoalAttain = false;
            this->stoppedOnTimeLimit = false;
            this->callbackFailed = false;

            if (!X0_is_scaled)
            {
                this->scaleX0();
            }
            else
            {
                this->unscaleX0();
            }

            this->X_scaled = this->X0_scaled;
            this->X_unscaled = this->X0_unscaled;
            this->evaluate_current_point(false);
            if (!this->current_functions_are_finite(false))
                throw std::runtime_error("IPOPT initial objective or constraint vector contains NaN or infinity.");

            for (size_t Xindex = 0; Xindex < this->nX; ++Xindex)
            {
                this->ipopt_x[Xindex] = this->X_scaled[Xindex] _GETVALUE;
                this->ipopt_bound_multipliers_lower[Xindex] = 0.0;
                this->ipopt_bound_multipliers_upper[Xindex] = 0.0;
            }

            std::fill(this->ipopt_constraint_multipliers.begin(), this->ipopt_constraint_multipliers.end(), 0.0);
            std::fill(this->ipopt_constraints.begin(), this->ipopt_constraints.end(), 0.0);

            this->NLP_start_time = time(NULL);
            this->mostRecentNLPWriteTime = time(NULL);
            this->newBestIncumbent = false;
            this->first_feasibility = false;
            this->feasibility_metric_NLP_incumbent = 1.0e+101;
            this->J_NLP_incumbent = math::LARGE;
            this->movie_frame_count = 0;

            ipnumber finalObjective = 0.0;
            const ApplicationReturnStatus status = IpoptSolve(this->myIPOPT,
                this->ipopt_x.data(),
                this->ipopt_constraints.empty() ? nullptr : this->ipopt_constraints.data(),
                &finalObjective,
                this->ipopt_constraint_multipliers.empty() ? nullptr : this->ipopt_constraint_multipliers.data(),
                this->ipopt_bound_multipliers_lower.data(),
                this->ipopt_bound_multipliers_upper.data(),
                this);

            for (size_t Xindex = 0; Xindex < this->nX; ++Xindex)
            {
                this->X_scaled[Xindex] = this->ipopt_x[Xindex];
            }
            this->unscaleX();
            this->evaluate_current_point(false);

            this->myProblem->check_feasibility(this->X_unscaled,
                this->F,
                this->worst_decision_variable,
                this->worst_constraint,
                this->feasibility_metric,
                this->normalized_feasibility_metric,
                this->distance_from_equality_filament,
                this->decision_vector_feasibility_metric);

            const double worst_exit_feasibility = fmax(this->normalized_feasibility_metric, this->decision_vector_feasibility_metric);

            if (this->myOptions.get_enable_NLP_chaperone())
            {
                this->unscaleX_NLP_incumbent();
                if (worst_exit_feasibility < this->myOptions.get_feasibility_tolerance()
                    && this->feasibility_metric_NLP_incumbent < this->myOptions.get_feasibility_tolerance())
                {
                    if (this->J_NLP_incumbent < this->F.front())
                    {
                        this->X_unscaled = this->X_NLP_incumbent_unscaled;
                        this->X_scaled = this->X_NLP_incumbent_scaled;
                        this->F = this->F_NLP_incumbent;
                    }
                }
                else if (this->feasibility_metric_NLP_incumbent < worst_exit_feasibility
                    && this->feasibility_metric_NLP_incumbent < this->myOptions.get_feasibility_tolerance())
                {
                    this->X_unscaled = this->X_NLP_incumbent_unscaled;
                    this->X_scaled = this->X_NLP_incumbent_scaled;
                    this->F = this->F_NLP_incumbent;
                }
                else if (this->feasibility_metric_NLP_incumbent < worst_exit_feasibility)
                {
                    this->X_unscaled = this->X_NLP_incumbent_unscaled;
                    this->X_scaled = this->X_NLP_incumbent_scaled;
                    this->F = this->F_NLP_incumbent;
                }
            }

            // The chaperone may replace Ipopt's exit point. Recompute all public
            // feasibility metrics for the point EMTG will actually return.
            this->myProblem->check_feasibility(this->X_unscaled,
                this->F,
                this->worst_decision_variable,
                this->worst_constraint,
                this->feasibility_metric,
                this->normalized_feasibility_metric,
                this->distance_from_equality_filament,
                this->decision_vector_feasibility_metric);

            const double returnedPointFeasibility = fmax(
                this->normalized_feasibility_metric,
                this->decision_vector_feasibility_metric);
            const IPOPTStatusMapping statusMapping = MapIPOPTStatus(
                status,
                this->stoppedOnGoalAttain,
                this->stoppedOnTimeLimit,
                this->callbackFailed);
            const bool returnedPointIsAcceptable = statusMapping.solverAcceptedPoint
                && returnedPointFeasibility <= this->myOptions.get_feasibility_tolerance()
                && this->current_functions_are_finite(false);
            this->set_solver_status(
                statusMapping.status,
                static_cast<int>(status),
                returnedPointIsAcceptable);
        }

        bool IPOPT_interface::process_current_iteration()
        {
            bool wroteToFile = false;

            double feasibility = 0.0;
            double normalizedFeasibility = 0.0;
            double distance_from_equality_filament = 0.0;
            size_t worst_constraint = 0;
            size_t worst_decision_variable = 0;
            double decision_variable_feasibility_metric = 0.0;

            try
            {
                this->myProblem->check_feasibility(this->X_unscaled,
                    this->F,
                    worst_decision_variable,
                    worst_constraint,
                    feasibility,
                    normalizedFeasibility,
                    distance_from_equality_filament,
                    decision_variable_feasibility_metric,
                    true);
            }
            catch (std::runtime_error& runtime_error)
            {
                this->report_callback_failure("intermediate feasibility check", runtime_error.what());
                return false;
            }

            const double worstFeasibility = fmax(normalizedFeasibility, decision_variable_feasibility_metric);

            if (this->myOptions.get_stop_on_goal_attain()
                && worstFeasibility < this->myOptions.get_feasibility_tolerance()
                && this->myProblem->getUnscaledObjective() < this->myOptions.get_objective_goal())
            {
                if (!this->myOptions.get_quiet_NLP())
                {
                    std::cout << "NLP goal satisfied, exiting NLP" << std::endl;
                }
                this->stoppedOnGoalAttain = true;
                return false;
            }

            if (this->myOptions.get_enable_NLP_chaperone())
            {
                if (worstFeasibility < this->myOptions.get_feasibility_tolerance()
                && this->feasibility_metric_NLP_incumbent < this->myOptions.get_feasibility_tolerance())
                {
                    if (this->F.front() < this->J_NLP_incumbent)
                    {
                        this->J_NLP_incumbent = this->F.front();
                        this->feasibility_metric_NLP_incumbent = worstFeasibility;
                        this->X_NLP_incumbent_unscaled = this->X_unscaled;
                        this->X_NLP_incumbent_scaled = this->X_scaled;
                        this->F_NLP_incumbent = this->F;

                        if (this->J_NLP_incumbent < this->JGlobalIncumbent)
                        {
                            this->newBestIncumbent = true;
                            this->JGlobalIncumbent = this->J_NLP_incumbent;
                        }
                    }

                    if (!this->first_feasibility)
                    {
                        this->first_feasibility = true;

                        if (this->J_NLP_incumbent < this->JGlobalIncumbent)
                        {
                            this->newBestIncumbent = true;
                            this->JGlobalIncumbent = this->J_NLP_incumbent;

                            this->myProblem->Xopt = this->X_NLP_incumbent_unscaled;
                            this->myProblem->F = this->F_NLP_incumbent;
                            this->myProblem->evaluate(this->X_NLP_incumbent_unscaled, this->F_NLP_incumbent, this->G, false);
                            this->reset_evaluation_cache();

                            this->myProblem->what_the_heck_am_I_called(SolutionOutputType::SUCCESS);
                            this->myProblem->output(this->myProblem->options.outputfile);
                            wroteToFile = true;
                            this->mostRecentNLPWriteTime = time(NULL);
                            this->newBestIncumbent = false;
                        }
                    }
                }
                else if (worstFeasibility < this->feasibility_metric_NLP_incumbent)
                {
                    this->J_NLP_incumbent = this->F.front();
                    this->feasibility_metric_NLP_incumbent = worstFeasibility;
                    this->X_NLP_incumbent_unscaled = this->X_unscaled;
                    this->X_NLP_incumbent_scaled = this->X_scaled;
                    this->F_NLP_incumbent = this->F;

                    if (worstFeasibility < this->myOptions.get_feasibility_tolerance()
                        && !this->first_feasibility)
                    {
                        this->first_feasibility = true;

                        if (this->J_NLP_incumbent < this->JGlobalIncumbent)
                        {
                            this->newBestIncumbent = true;
                            this->JGlobalIncumbent = this->J_NLP_incumbent;

                            this->myProblem->Xopt = this->X_NLP_incumbent_unscaled;
                            this->myProblem->F = this->F_NLP_incumbent;
                            this->myProblem->evaluate(this->X_NLP_incumbent_unscaled, this->F_NLP_incumbent, this->G, false);
                            this->reset_evaluation_cache();

                            this->myProblem->what_the_heck_am_I_called(SolutionOutputType::SUCCESS);
                            this->myProblem->output(this->myProblem->options.outputfile);

                            wroteToFile = true;
                            this->mostRecentNLPWriteTime = time(NULL);
                            this->newBestIncumbent = false;
                        }
                    }
                }

                const time_t now = time(NULL);
                if ((now - this->mostRecentNLPWriteTime) > this->myProblem->options.NLP_write_output_check_time
                    && this->newBestIncumbent
                    && !wroteToFile)
                {
                    this->myProblem->Xopt = this->X_NLP_incumbent_unscaled;
                    this->myProblem->F = this->F_NLP_incumbent;
                    this->myProblem->evaluate(this->X_NLP_incumbent_unscaled, this->F_NLP_incumbent, this->G, false);
                    this->reset_evaluation_cache();
                    this->myProblem->what_the_heck_am_I_called(SolutionOutputType::SUCCESS);
                    this->myProblem->output(this->myProblem->options.outputfile);

                    if (!this->myOptions.get_quiet_NLP())
                        std::cout << "Intermediate NLP solution written to file with new best J = " << this->JGlobalIncumbent << "." << std::endl;

                    this->mostRecentNLPWriteTime = time(NULL);
                    this->newBestIncumbent = false;
                }
            }

            const time_t now = time(NULL);
            if (this->myOptions.get_print_NLP_movie_frames())
            {
                this->myProblem->X = this->X_unscaled;
                this->myProblem->F = this->F;
                this->myProblem->output_problem_bounds_and_descriptions(this->myProblem->options.working_directory + "//" + "NLP_frame_" + std::to_string(this->movie_frame_count++) + ".csv");
            }

            if (now - this->NLP_start_time > this->myOptions.get_max_run_time_seconds())
            {
                if (!this->myOptions.get_quiet_NLP())
                {
                    std::cout << "Exceeded NLP time limit of " << this->myOptions.get_max_run_time_seconds() << " seconds. Aborting NLP run." << std::endl;
                }
                this->stoppedOnTimeLimit = true;
                return false;
            }

            return true;
        }

        bool IPOPT_interface::set_current_scaled_x(const ipindex n, const ipnumber* x)
        {
            if (n != static_cast<ipindex>(this->nX) || (n > 0 && x == nullptr))
                return false;

            for (size_t Xindex = 0; Xindex < this->nX; ++Xindex)
            {
                if (!std::isfinite(x[Xindex]))
                    return false;
                this->X_scaled[Xindex] = x[Xindex];
            }
            this->unscaleX();
            for (size_t Xindex = 0; Xindex < this->nX; ++Xindex)
            {
                if (!std::isfinite(this->X_unscaled[Xindex] _GETVALUE))
                    return false;
            }
            return true;
        }

        bool IPOPT_interface::current_functions_are_finite(const bool needG) const
        {
            if (this->F.size() != this->nF || (needG && this->G.size() != this->nG))
                return false;

            for (size_t Findex = 0; Findex < this->nF; ++Findex)
            {
                if (!std::isfinite(this->F[Findex] _GETVALUE))
                    return false;
            }
            if (needG)
            {
                for (size_t Gindex = 0; Gindex < this->nG; ++Gindex)
                {
                    if (!std::isfinite(this->G[Gindex]))
                        return false;
                }
            }
            return true;
        }

        void IPOPT_interface::report_callback_failure(const char* callbackName, const char* message)
        {
            this->callbackFailed = true;
            if (!this->myOptions.get_quiet_NLP())
            {
                std::cout << "IPOPT " << callbackName << " failed";
                if (message)
                    std::cout << ": " << message;
                std::cout << std::endl;
            }
        }

        bool IPOPT_CALLCONV IPOPT_interface::evaluate_objective(ipindex n,
            ipnumber* x,
            bool,
            ipnumber* obj_value,
            UserDataPtr user_data)
        {
            IPOPT_interface* self = static_cast<IPOPT_interface*>(user_data);

            try
            {
                if (!self || !obj_value || !self->set_current_scaled_x(n, x))
                {
                    if (self)
                        self->report_callback_failure("objective callback", "invalid dimension, pointer, or decision-vector value");
                    return false;
                }
                self->evaluate_current_point(false);
                if (!self->current_functions_are_finite(false))
                {
                    self->report_callback_failure("objective callback", "objective or constraint value is NaN or infinity");
                    return false;
                }

                if (self->myOptions.get_SolverMode() == NLPMode::FeasiblePoint)
                {
                    *obj_value = 0.0;
                }
                else
                {
                    *obj_value = self->F.front() _GETVALUE;
                }

                return true;
            }
            catch (std::exception& error)
            {
                self->report_callback_failure("objective callback", error.what());
                return false;
            }
            catch (...)
            {
                self->report_callback_failure("objective callback", "non-standard exception");
                return false;
            }
        }

        bool IPOPT_CALLCONV IPOPT_interface::evaluate_objective_gradient(ipindex n,
            ipnumber* x,
            bool,
            ipnumber* grad_f,
            UserDataPtr user_data)
        {
            IPOPT_interface* self = static_cast<IPOPT_interface*>(user_data);

            try
            {
                if (!self || !grad_f || !self->set_current_scaled_x(n, x))
                {
                    if (self)
                        self->report_callback_failure("objective-gradient callback", "invalid dimension, pointer, or decision-vector value");
                    return false;
                }
                std::fill(grad_f, grad_f + self->nX, 0.0);

                if (self->myOptions.get_SolverMode() == NLPMode::FeasiblePoint)
                {
                    self->evaluate_current_point(false);
                    if (!self->current_functions_are_finite(false))
                    {
                        self->report_callback_failure("objective-gradient callback", "objective or constraint value is NaN or infinity");
                        return false;
                    }
                    return true;
                }

                self->evaluate_current_point(true);
                if (!self->current_functions_are_finite(true))
                {
                    self->report_callback_failure("objective-gradient callback", "function or derivative value is NaN or infinity");
                    return false;
                }

                for (size_t Aindex = 0; Aindex < self->nA; ++Aindex)
                {
                    if (self->iAfun[Aindex] == 0)
                    {
                        grad_f[self->jAvar[Aindex]] += self->A[Aindex];
                    }
                }

                for (size_t Gindex = 0; Gindex < self->nG; ++Gindex)
                {
                    if (self->iGfun[Gindex] == 0)
                    {
                        grad_f[self->jGvar[Gindex]] += self->G[Gindex];
                    }
                }

                for (size_t Xindex = 0; Xindex < self->nX; ++Xindex)
                {
                    if (!std::isfinite(grad_f[Xindex]))
                    {
                        self->report_callback_failure("objective-gradient callback", "assembled gradient is NaN or infinity");
                        return false;
                    }
                }

                return true;
            }
            catch (std::exception& error)
            {
                self->report_callback_failure("objective-gradient callback", error.what());
                return false;
            }
            catch (...)
            {
                self->report_callback_failure("objective-gradient callback", "non-standard exception");
                return false;
            }
        }

        bool IPOPT_CALLCONV IPOPT_interface::evaluate_constraints(ipindex n,
            ipnumber* x,
            bool,
            ipindex m,
            ipnumber* g,
            UserDataPtr user_data)
        {
            IPOPT_interface* self = static_cast<IPOPT_interface*>(user_data);

            try
            {
                if (!self
                    || m != static_cast<ipindex>(self->nF - 1)
                    || (m > 0 && g == nullptr)
                    || !self->set_current_scaled_x(n, x))
                {
                    if (self)
                        self->report_callback_failure("constraint callback", "invalid dimension, pointer, or decision-vector value");
                    return false;
                }
                self->evaluate_current_point(false);
                if (!self->current_functions_are_finite(false))
                {
                    self->report_callback_failure("constraint callback", "objective or constraint value is NaN or infinity");
                    return false;
                }

                for (size_t Findex = 1; Findex < self->nF; ++Findex)
                {
                    g[Findex - 1] = self->F[Findex] _GETVALUE;
                }

                return true;
            }
            catch (std::exception& error)
            {
                self->report_callback_failure("constraint callback", error.what());
                return false;
            }
            catch (...)
            {
                self->report_callback_failure("constraint callback", "non-standard exception");
                return false;
            }
        }

        bool IPOPT_CALLCONV IPOPT_interface::evaluate_jacobian(ipindex n,
            ipnumber* x,
            bool,
            ipindex m,
            ipindex nele_jac,
            ipindex* iRow,
            ipindex* jCol,
            ipnumber* values,
            UserDataPtr user_data)
        {
            IPOPT_interface* self = static_cast<IPOPT_interface*>(user_data);

            if (!self
                || m != static_cast<ipindex>(self->nF - 1)
                || nele_jac != static_cast<ipindex>(self->jacobianEntries.size()))
            {
                if (self)
                    self->report_callback_failure("Jacobian callback", "invalid dimensions");
                return false;
            }

            if (values == nullptr)
            {
                if (!iRow || !jCol)
                {
                    self->report_callback_failure("Jacobian structure callback", "null sparsity pointer");
                    return false;
                }
                for (size_t entryIndex = 0; entryIndex < self->jacobianEntries.size(); ++entryIndex)
                {
                    iRow[entryIndex] = self->jacobian_iRow[entryIndex];
                    jCol[entryIndex] = self->jacobian_jCol[entryIndex];
                }
                return true;
            }

            try
            {
                if (!self->set_current_scaled_x(n, x))
                {
                    self->report_callback_failure("Jacobian callback", "invalid decision-vector value");
                    return false;
                }
                self->evaluate_current_point(true);
                if (!self->current_functions_are_finite(true))
                {
                    self->report_callback_failure("Jacobian callback", "function or derivative value is NaN or infinity");
                    return false;
                }

                for (size_t entryIndex = 0; entryIndex < self->jacobianEntries.size(); ++entryIndex)
                {
                    const JacobianEntry& entry = self->jacobianEntries[entryIndex];
                    ipnumber value = 0.0;
                    for (const size_t Aindex : entry.linearSourceIndices)
                        value += self->A[Aindex];
                    for (const size_t Gindex : entry.nonlinearSourceIndices)
                        value += self->G[Gindex];
                    if (!std::isfinite(value))
                    {
                        self->report_callback_failure("Jacobian callback", "assembled Jacobian entry is NaN or infinity");
                        return false;
                    }
                    values[entryIndex] = value;
                }

                return true;
            }
            catch (std::exception& error)
            {
                self->report_callback_failure("Jacobian callback", error.what());
                return false;
            }
            catch (...)
            {
                self->report_callback_failure("Jacobian callback", "non-standard exception");
                return false;
            }
        }

        bool IPOPT_CALLCONV IPOPT_interface::evaluate_hessian(ipindex,
            ipnumber*,
            bool,
            ipnumber,
            ipindex,
            ipnumber*,
            bool,
            ipindex,
            ipindex*,
            ipindex*,
            ipnumber*,
            UserDataPtr)
        {
            // EMTG currently relies on Ipopt's limited-memory Hessian
            // approximation. The C interface still requires a non-null Hessian
            // callback, so provide a no-op implementation to satisfy that
            // contract.
            return true;
        }

        bool IPOPT_CALLCONV IPOPT_interface::intermediate_callback(ipindex,
            ipindex,
            ipnumber,
            ipnumber,
            ipnumber,
            ipnumber,
            ipnumber,
            ipnumber,
            ipnumber,
            ipnumber,
            ipindex,
            UserDataPtr user_data)
        {
            IPOPT_interface* self = static_cast<IPOPT_interface*>(user_data);

            try
            {
                if (!GetIpoptCurrentIterate(self->myIPOPT,
                    false,
                    static_cast<ipindex>(self->nX),
                    self->ipopt_x.data(),
                    nullptr,
                    nullptr,
                    static_cast<ipindex>(self->nF > 0 ? self->nF - 1 : 0),
                    nullptr,
                    nullptr))
                {
                    self->report_callback_failure("intermediate callback", "Ipopt did not provide its current iterate");
                    return false;
                }

                if (!self->set_current_scaled_x(static_cast<ipindex>(self->nX), self->ipopt_x.data()))
                {
                    self->report_callback_failure("intermediate callback", "current iterate contains NaN or infinity");
                    return false;
                }
                self->evaluate_current_point(false);
                if (!self->current_functions_are_finite(false))
                {
                    self->report_callback_failure("intermediate callback", "objective or constraint value is NaN or infinity");
                    return false;
                }

                // Goal, time, and movie-frame behavior is solver-neutral and
                // must not depend on whether incumbent chaperoning is enabled.
                if (!self->process_current_iteration())
                    return false;

                return true;
            }
            catch (std::exception& error)
            {
                self->report_callback_failure("intermediate callback", error.what());
                return false;
            }
            catch (...)
            {
                self->report_callback_failure("intermediate callback", "non-standard exception");
                return false;
            }
        }
    }//end namespace Solvers
}//end namespace EMTG
