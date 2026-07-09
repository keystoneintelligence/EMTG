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
#include <ctime>
#include <iostream>
#include <stdexcept>

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

        IPOPT_interface::IPOPT_interface(problem* myProblem,
            const NLPoptions& myOptions) :
            NLP_interface::NLP_interface(myProblem, myOptions),
            myIPOPT(nullptr),
            stoppedOnGoalAttain(false),
            stoppedOnTimeLimit(false)
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

            this->ipopt_variable_lower_bounds.resize(this->nX, 0.0);
            this->ipopt_variable_upper_bounds.resize(this->nX, 0.0);
            this->ipopt_x.resize(this->nX, 0.0);
            this->ipopt_bound_multipliers_lower.resize(this->nX, 0.0);
            this->ipopt_bound_multipliers_upper.resize(this->nX, 0.0);
            this->ipopt_objective_gradient.resize(this->nX, 0.0);

            for (size_t Xindex = 0; Xindex < this->nX; ++Xindex)
            {
                this->Xlowerbounds[Xindex] = 0.0;
                this->Xupperbounds[Xindex] = (this->myProblem->Xupperbounds[Xindex] - this->myProblem->Xlowerbounds[Xindex]) / this->myProblem->X_scale_factors[Xindex];
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
                    this->ipopt_constraint_lower_bounds[Findex - 1] = this->Flowerbounds[Findex];
                    this->ipopt_constraint_upper_bounds[Findex - 1] = this->Fupperbounds[Findex];
                }
            }

            this->jacobian_iRow.clear();
            this->jacobian_jCol.clear();
            this->jacobianEntries.clear();

            for (size_t Aindex = 0; Aindex < this->nA; ++Aindex)
            {
                if (this->iAfun[Aindex] > 0)
                {
                    this->jacobian_iRow.push_back(static_cast<ipindex>(this->iAfun[Aindex] - 1));
                    this->jacobian_jCol.push_back(static_cast<ipindex>(this->jAvar[Aindex]));
                    this->jacobianEntries.push_back({ true, Aindex });
                }
            }

            for (size_t Gindex = 0; Gindex < this->nG; ++Gindex)
            {
                if (this->iGfun[Gindex] > 0)
                {
                    this->jacobian_iRow.push_back(static_cast<ipindex>(this->iGfun[Gindex] - 1));
                    this->jacobian_jCol.push_back(static_cast<ipindex>(this->jGvar[Gindex]));
                    this->jacobianEntries.push_back({ false, Gindex });
                }
            }

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

            AddIpoptStrOption(this->myIPOPT, mutable_string("linear_solver"), mutable_string("mumps"));
            AddIpoptStrOption(this->myIPOPT, mutable_string("hessian_approximation"), mutable_string("limited-memory"));
            AddIpoptIntOption(this->myIPOPT, mutable_string("max_iter"), static_cast<ipindex>(this->myOptions.get_major_iterations_limit()));
            AddIpoptNumOption(this->myIPOPT, mutable_string("tol"), this->myOptions.get_optimality_tolerance());
            AddIpoptNumOption(this->myIPOPT, mutable_string("constr_viol_tol"), this->myOptions.get_feasibility_tolerance());
            AddIpoptNumOption(this->myIPOPT, mutable_string("acceptable_tol"), this->myOptions.get_feasibility_tolerance());
            AddIpoptNumOption(this->myIPOPT, mutable_string("max_wall_time"), static_cast<ipnumber>(this->myOptions.get_max_run_time_seconds()));
            AddIpoptNumOption(this->myIPOPT, mutable_string("bound_relax_factor"), 0.0);

            if (this->myOptions.get_check_derivatives())
            {
                AddIpoptStrOption(this->myIPOPT, mutable_string("derivative_test"), mutable_string("first-order"));
            }

            if (this->myOptions.get_quiet_NLP())
            {
                AddIpoptIntOption(this->myIPOPT, mutable_string("print_level"), 0);
                AddIpoptStrOption(this->myIPOPT, mutable_string("sb"), mutable_string("yes"));
            }
            else
            {
                AddIpoptIntOption(this->myIPOPT, mutable_string("print_level"), 5);
            }

            const std::string output_file_path = this->myOptions.get_output_file_path();
            if (!output_file_path.empty())
            {
                OpenIpoptOutputFile(this->myIPOPT, mutable_string(output_file_path.c_str()), this->myOptions.get_quiet_NLP() ? 0 : 5);
            }

            SetIntermediateCallback(this->myIPOPT, IPOPT_interface::intermediate_callback);
        }

        void IPOPT_interface::run_NLP(const bool& X0_is_scaled)
        {
            this->reset_solver_state("IPOPT");
            this->stoppedOnGoalAttain = false;
            this->stoppedOnTimeLimit = false;

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

            NLPSolveStatus mappedStatus = NLPSolveStatus::Failed;
            bool acceptableTermination = false;
            switch (status)
            {
            case Solve_Succeeded:
                mappedStatus = NLPSolveStatus::Converged;
                acceptableTermination = true;
                break;
            case Solved_To_Acceptable_Level:
            case Search_Direction_Becomes_Too_Small:
                mappedStatus = NLPSolveStatus::Acceptable;
                acceptableTermination = true;
                break;
            case Feasible_Point_Found:
                mappedStatus = NLPSolveStatus::FeasiblePoint;
                acceptableTermination = true;
                break;
            case Maximum_Iterations_Exceeded:
                mappedStatus = NLPSolveStatus::MaxIterations;
                break;
            case Maximum_CpuTime_Exceeded:
            case Maximum_WallTime_Exceeded:
                mappedStatus = NLPSolveStatus::MaxTime;
                break;
            case User_Requested_Stop:
                mappedStatus = this->stoppedOnTimeLimit ? NLPSolveStatus::MaxTime : NLPSolveStatus::UserStopped;
                acceptableTermination = this->stoppedOnGoalAttain;
                break;
            default:
                mappedStatus = NLPSolveStatus::Failed;
                break;
            }
            this->set_solver_status(mappedStatus, static_cast<int>(status), acceptableTermination);

            this->myProblem->check_feasibility(this->X_unscaled,
                this->F,
                this->worst_decision_variable,
                this->worst_constraint,
                this->feasibility_metric,
                this->normalized_feasibility_metric,
                this->distance_from_equality_filament,
                this->decision_vector_feasibility_metric);

            const double worst_feasibility = fmax(this->normalized_feasibility_metric, this->decision_vector_feasibility_metric);

            if (this->myOptions.get_enable_NLP_chaperone())
            {
                this->unscaleX_NLP_incumbent();
                if (worst_feasibility < this->myOptions.get_feasibility_tolerance()
                    && this->feasibility_metric_NLP_incumbent < this->myOptions.get_feasibility_tolerance())
                {
                    if (this->J_NLP_incumbent < this->F.front())
                    {
                        this->X_unscaled = this->X_NLP_incumbent_unscaled;
                        this->X_scaled = this->X_NLP_incumbent_scaled;
                        this->F = this->F_NLP_incumbent;
                    }
                }
                else if (this->feasibility_metric_NLP_incumbent < worst_feasibility
                    && this->feasibility_metric_NLP_incumbent < this->myOptions.get_feasibility_tolerance())
                {
                    this->X_unscaled = this->X_NLP_incumbent_unscaled;
                    this->X_scaled = this->X_NLP_incumbent_scaled;
                    this->F = this->F_NLP_incumbent;
                }
                else if (this->feasibility_metric_NLP_incumbent < worst_feasibility)
                {
                    this->X_unscaled = this->X_NLP_incumbent_unscaled;
                    this->X_scaled = this->X_NLP_incumbent_scaled;
                    this->F = this->F_NLP_incumbent;
                }
            }
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
                if (!this->myOptions.get_quiet_NLP())
                {
                    std::cout << runtime_error.what() << std::endl << std::endl;
                }
                return false;
            }

            if (this->myOptions.get_stop_on_goal_attain()
                && normalizedFeasibility < this->myOptions.get_feasibility_tolerance()
                && this->myProblem->getUnscaledObjective() < this->myOptions.get_objective_goal())
            {
                if (!this->myOptions.get_quiet_NLP())
                {
                    std::cout << "NLP goal satisfied, exiting NLP" << std::endl;
                }
                this->stoppedOnGoalAttain = true;
                return false;
            }

            const double worstFeasibility = fmax(normalizedFeasibility, decision_variable_feasibility_metric);

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
                {
                    std::cout << "Intermediate NLP solution written to file with new best J = " << this->JGlobalIncumbent << "." << std::endl;
                }

                this->mostRecentNLPWriteTime = time(NULL);
                this->newBestIncumbent = false;
            }

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

        bool IPOPT_CALLCONV IPOPT_interface::evaluate_objective(ipindex n,
            ipnumber* x,
            bool,
            ipnumber* obj_value,
            UserDataPtr user_data)
        {
            IPOPT_interface* self = static_cast<IPOPT_interface*>(user_data);

            try
            {
                for (size_t Xindex = 0; Xindex < self->nX; ++Xindex)
                {
                    self->X_scaled[Xindex] = x[Xindex];
                }
                self->unscaleX();
                self->evaluate_current_point(false);

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
                if (!self->myOptions.get_quiet_NLP())
                {
                    std::cout << error.what() << std::endl;
                }
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
                for (size_t Xindex = 0; Xindex < self->nX; ++Xindex)
                {
                    self->X_scaled[Xindex] = x[Xindex];
                    grad_f[Xindex] = 0.0;
                }
                self->unscaleX();

                if (self->myOptions.get_SolverMode() == NLPMode::FeasiblePoint)
                {
                    self->evaluate_current_point(false);
                    return true;
                }

                self->evaluate_current_point(true);

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

                return true;
            }
            catch (std::exception& error)
            {
                if (!self->myOptions.get_quiet_NLP())
                {
                    std::cout << error.what() << std::endl;
                }
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
                for (size_t Xindex = 0; Xindex < self->nX; ++Xindex)
                {
                    self->X_scaled[Xindex] = x[Xindex];
                }
                self->unscaleX();
                self->evaluate_current_point(false);

                for (size_t Findex = 1; Findex < self->nF; ++Findex)
                {
                    g[Findex - 1] = self->F[Findex] _GETVALUE;
                }

                return true;
            }
            catch (std::exception& error)
            {
                if (!self->myOptions.get_quiet_NLP())
                {
                    std::cout << error.what() << std::endl;
                }
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

            if (values == nullptr)
            {
                for (size_t entryIndex = 0; entryIndex < self->jacobianEntries.size(); ++entryIndex)
                {
                    iRow[entryIndex] = self->jacobian_iRow[entryIndex];
                    jCol[entryIndex] = self->jacobian_jCol[entryIndex];
                }
                return true;
            }

            try
            {
                for (size_t Xindex = 0; Xindex < self->nX; ++Xindex)
                {
                    self->X_scaled[Xindex] = x[Xindex];
                }
                self->unscaleX();
                self->evaluate_current_point(true);

                for (size_t entryIndex = 0; entryIndex < self->jacobianEntries.size(); ++entryIndex)
                {
                    const JacobianEntry& entry = self->jacobianEntries[entryIndex];
                    values[entryIndex] = entry.isLinear ? self->A[entry.sourceIndex] : self->G[entry.sourceIndex];
                }

                return true;
            }
            catch (std::exception& error)
            {
                if (!self->myOptions.get_quiet_NLP())
                {
                    std::cout << error.what() << std::endl;
                }
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
                    return false;
                }

                for (size_t Xindex = 0; Xindex < self->nX; ++Xindex)
                {
                    self->X_scaled[Xindex] = self->ipopt_x[Xindex];
                }
                self->unscaleX();
                self->evaluate_current_point(false);

                if (self->myOptions.get_enable_NLP_chaperone() && !self->process_current_iteration())
                {
                    return false;
                }

                if (!self->myOptions.get_enable_NLP_chaperone())
                {
                    if (self->myOptions.get_print_NLP_movie_frames())
                    {
                        self->myProblem->X = self->X_unscaled;
                        self->myProblem->F = self->F;
                        self->myProblem->output_problem_bounds_and_descriptions(self->myProblem->options.working_directory + "//" + "NLP_frame_" + std::to_string(self->movie_frame_count++) + ".csv");
                    }

                    if (time(NULL) - self->NLP_start_time > self->myOptions.get_max_run_time_seconds())
                    {
                        self->stoppedOnTimeLimit = true;
                        return false;
                    }
                }

                return true;
            }
            catch (std::exception& error)
            {
                if (!self->myOptions.get_quiet_NLP())
                {
                    std::cout << error.what() << std::endl;
                }
                return false;
            }
        }
    }//end namespace Solvers
}//end namespace EMTG
