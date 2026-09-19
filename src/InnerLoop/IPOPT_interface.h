// EMTG: Evolutionary Mission Trajectory Generator
// An open-source global optimization tool for preliminary mission design
// Provided by NASA Goddard Space Flight Center
//
// Copyright (c) 2013 - 2024 United States Government as represented by the
// Administrator of the National Aeronautics and Space Administration.
// All Other Rights Reserved.

#pragma once

#include "NLP_interface.h"

#include "IpStdCInterface.h"

namespace EMTG
{
    namespace Solvers
    {
        struct IPOPTStatusMapping
        {
            NLPSolveStatus status;
            bool solverAcceptedPoint;
        };

        IPOPTStatusMapping MapIPOPTStatus(const ApplicationReturnStatus status,
            const bool stoppedOnGoalAttain,
            const bool stoppedOnTimeLimit,
            const bool callbackFailed);

        class IPOPT_interface : public NLP_interface
        {
        public:
            IPOPT_interface() : NLP_interface::NLP_interface(), myIPOPT(nullptr), stoppedOnGoalAttain(false), stoppedOnTimeLimit(false), callbackFailed(false) {};
            IPOPT_interface(problem* myProblem,
                const NLPoptions& myOptions);
            virtual ~IPOPT_interface();

            virtual void run_NLP(const bool& X0_is_scaled = true);

        private:
            struct JacobianEntry
            {
                std::vector<size_t> linearSourceIndices;
                std::vector<size_t> nonlinearSourceIndices;
            };

            void initialize_problem();
            bool process_current_iteration();
            bool set_current_scaled_x(const ipindex n, const ipnumber* x);
            bool current_functions_are_finite(const bool needG) const;
            void report_callback_failure(const char* callbackName, const char* message = nullptr);

            static bool IPOPT_CALLCONV evaluate_objective(ipindex n,
                ipnumber* x,
                bool new_x,
                ipnumber* obj_value,
                UserDataPtr user_data);
            static bool IPOPT_CALLCONV evaluate_objective_gradient(ipindex n,
                ipnumber* x,
                bool new_x,
                ipnumber* grad_f,
                UserDataPtr user_data);
            static bool IPOPT_CALLCONV evaluate_constraints(ipindex n,
                ipnumber* x,
                bool new_x,
                ipindex m,
                ipnumber* g,
                UserDataPtr user_data);
            static bool IPOPT_CALLCONV evaluate_jacobian(ipindex n,
                ipnumber* x,
                bool new_x,
                ipindex m,
                ipindex nele_jac,
                ipindex* iRow,
                ipindex* jCol,
                ipnumber* values,
                UserDataPtr user_data);
            static bool IPOPT_CALLCONV evaluate_hessian(ipindex n,
                ipnumber* x,
                bool new_x,
                ipnumber obj_factor,
                ipindex m,
                ipnumber* lambda,
                bool new_lambda,
                ipindex nele_hess,
                ipindex* iRow,
                ipindex* jCol,
                ipnumber* values,
                UserDataPtr user_data);
            static bool IPOPT_CALLCONV intermediate_callback(ipindex alg_mod,
                ipindex iter_count,
                ipnumber obj_value,
                ipnumber inf_pr,
                ipnumber inf_du,
                ipnumber mu,
                ipnumber d_norm,
                ipnumber regularization_size,
                ipnumber alpha_du,
                ipnumber alpha_pr,
                ipindex ls_trials,
                UserDataPtr user_data);

            IpoptProblem myIPOPT;
            std::vector<ipnumber> ipopt_x;
            std::vector<ipnumber> ipopt_constraints;
            std::vector<ipnumber> ipopt_constraint_multipliers;
            std::vector<ipnumber> ipopt_bound_multipliers_lower;
            std::vector<ipnumber> ipopt_bound_multipliers_upper;
            std::vector<ipnumber> ipopt_objective_gradient;
            std::vector<ipnumber> ipopt_variable_lower_bounds;
            std::vector<ipnumber> ipopt_variable_upper_bounds;
            std::vector<ipnumber> ipopt_constraint_lower_bounds;
            std::vector<ipnumber> ipopt_constraint_upper_bounds;
            std::vector<ipindex> jacobian_iRow;
            std::vector<ipindex> jacobian_jCol;
            std::vector<JacobianEntry> jacobianEntries;
            bool stoppedOnGoalAttain;
            bool stoppedOnTimeLimit;
            bool callbackFailed;
        };//end class IPOPT_interface
    }//end namespace Solvers
}//end namespace EMTG
