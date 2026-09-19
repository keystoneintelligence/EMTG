// EMTG: Evolutionary Mission Trajectory Generator
// An open-source global optimization tool for preliminary mission design
// Provided by NASA Goddard Space Flight Center
//
// Copyright (c) 2013 - 2024 United States Government as represented by the
// Administrator of the National Aeronautics and Space Administration.
// All Other Rights Reserved.

// Licensed under the NASA Open Source License (the "License"); 
// You may not use this file except in compliance with the License. 
// You may obtain a copy of the License at:
// https://opensource.org/licenses/NASA-1.3
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either 
// express or implied.   See the License for the specific language
// governing permissions and limitations under the License.

#include "ExplicitRungeKutta.h"
#include "Integrand.h"
#include "RungeKutta4Tableau.h"
#include "RungeKuttaDP87Tableau.h"

#include <algorithm>
#include <cmath>

namespace EMTG
{
    namespace Integration
    {

        //standard constructor
        ExplicitRungeKutta::ExplicitRungeKutta(Integrand * integrand_in,
            const IntegrationCoefficientsType & RK_type,
            const size_t & num_states_in,
            const size_t & STM_size_in) : IntegrationScheme(integrand_in, num_states_in, STM_size_in),
            f(num_states, 1, 0.0),
            y(num_states, 1, 0.0),
            STM(STM_size_in, math::identity),
            STM_stage(STM_size_in, math::identity),
            grad_vec(STM_size_in, 1, 0.0),
            error_step_size(0.0),
            error_step_includes_STM(false)
        {

            if (RK_type == IntegrationCoefficientsType::rkdp87)
            {
                this->RK_tableau = new RungeKuttaDP87Tableau();
            }
            else if (RK_type == IntegrationCoefficientsType::rk4)
            {
                this->RK_tableau = new RungeKutta4Tableau();
            }
            else
            {
                throw std::invalid_argument("The specified IntegrationCoefficientsType type is not recognized by the ExplicitRungeKutta constructor.");
            }

            this->num_stages = this->RK_tableau->getNumStages();

            this->A = this->RK_tableau->getA();
            this->bUpper = this->RK_tableau->getBUpper();
            if (this->RK_tableau->getHasVariableStepCoefficients())
            {
                this->bLower = this->RK_tableau->getBLower();
            }
            this->c = this->RK_tableau->getC();

            // the gradient_bin needs to have the same length as the STM dimension so 
            // we can perform matrix multiplies with it
            this->gradient_bin.resize(this->num_states, this->num_stages, 0.0);
            this->STM_bin.resize(this->num_stages);
            for (math::Matrix<double> & mat : this->STM_bin)
            {
                mat.resize(this->STM_size, this->STM_size, 0.0);
            }
        }

        //destructor
        ExplicitRungeKutta ::~ExplicitRungeKutta()
        {
            delete this->RK_tableau;
        }

        void ExplicitRungeKutta::storeStageGradient(const size_t & stage_index)
        {
            for (size_t k = 0; k < this->num_states; ++k)
            {
                this->grad_vec(k) = this->f(k) _GETVALUE;
                this->gradient_bin(k, stage_index) = this->f(k);
            }
        }

        void ExplicitRungeKutta::stateUpdateFromTableauRow(const math::Matrix<doubleType> & state_left,
            const size_t & coefficient_row,
            const size_t & stage_index,
            const doubleType & step_size)
        {
            this->storeStageGradient(stage_index);

            for (size_t k = 0; k < this->num_states; ++k)
            {
                this->y(k) = state_left(k);
                for (size_t j = 0; j < stage_index + 1; ++j)
                {
                    this->y(k) += this->gradient_bin(k, j) * this->A(coefficient_row, j) * step_size;
                }
            }
        }

        void ExplicitRungeKutta::stateUpdateFromWeights(const math::Matrix<doubleType> & state_left,
            const math::Matrix<double> & coefficients,
            const size_t & stage_index,
            const doubleType & step_size)
        {
            this->storeStageGradient(stage_index);

            for (size_t k = 0; k < this->num_states; ++k)
            {
                this->y(k) = state_left(k);
                for (size_t j = 0; j < stage_index + 1; ++j)
                {
                    this->y(k) += this->gradient_bin(k, j) * coefficients(j) * step_size;
                }
            }
        }

        void ExplicitRungeKutta::stmStageUpdate(const doubleType & step_size)
        {
            const math::Matrix<double>& fx = this->integrand->getStatePropMatRef();
            const double step_size_value = step_size _GETVALUE;
            const size_t last_state_index = this->STM_size - 1;

            for (size_t i = 0; i < this->STM_size; ++i)
            {
                for (size_t j = 0; j < this->STM_size; ++j)
                {
                    double stage_entry = 0.0;
                    for (size_t k = 0; k < this->STM_size; ++k)
                    {
                        double multiplier = fx(i, k) * step_size_value;
                        if (k == last_state_index)
                            multiplier += this->grad_vec(i) * this->dstep_sizedProp_var;

                        stage_entry += multiplier * this->STM(k, j);
                    }

                    this->STM_stage(i, j) = stage_entry;
                }
            }
        }

        void ExplicitRungeKutta::addScaledSTMStage(const math::Matrix<double> & coefficients,
            const size_t & stage_index)
        {
            for (size_t k = 0; k < stage_index + 1; ++k)
            {
                const double coefficient = coefficients(k);
                for (size_t i = 0; i < this->STM_size; ++i)
                {
                    for (size_t j = 0; j < this->STM_size; ++j)
                    {
                        this->STM(i, j) += this->STM_bin[k](i, j) * coefficient;
                    }
                }
            }
        }

        void ExplicitRungeKutta::addScaledSTMStageFromTableauRow(const size_t & coefficient_row,
            const size_t & stage_index)
        {
            for (size_t k = 0; k < stage_index + 1; ++k)
            {
                const double coefficient = this->A(coefficient_row, k);
                for (size_t i = 0; i < this->STM_size; ++i)
                {
                    for (size_t j = 0; j < this->STM_size; ++j)
                    {
                        this->STM(i, j) += this->STM_bin[k](i, j) * coefficient;
                    }
                }
            }
        }

        void ExplicitRungeKutta::stmUpdateFromTableauRow(const math::Matrix<double> & STM_left,
            const size_t & coefficient_row,
            const size_t & stage_index,
            const doubleType & step_size)
        {
            this->stmStageUpdate(step_size);
            this->STM_bin[stage_index].shallow_copy(this->STM_stage);
            this->STM.shallow_copy(STM_left);
            this->addScaledSTMStageFromTableauRow(coefficient_row, stage_index);
        }

        void ExplicitRungeKutta::stmUpdateFromWeights(const math::Matrix<double> & STM_left,
            const math::Matrix<double> & coefficients,
            const size_t & stage_index,
            const doubleType & step_size)
        {
            this->stmStageUpdate(step_size);
            this->STM_bin[stage_index].shallow_copy(this->STM_stage);
            this->STM.shallow_copy(STM_left);
            this->addScaledSTMStage(coefficients, stage_index);
        }

        void ExplicitRungeKutta::evaluateIntegrand(const math::Matrix<doubleType> & state_left,
            const size_t & stage_index,
            const doubleType & step_size,
            const bool & needSTM)
        {
            this->integrand->setCurrentIndependentVariable(*this->left_hand_independent_variable + this->c(stage_index) * step_size);
            this->integrand->evaluate(this->y, this->f, needSTM);
        }

        void ExplicitRungeKutta::evaluateIntegrand(const math::Matrix<doubleType> & state_left,
            const math::Matrix<doubleType> & control,
            const size_t & stage_index,
            const doubleType & step_size,
            const bool & needSTM)
        {
            this->integrand->setCurrentIndependentVariable(*this->left_hand_independent_variable + this->c(stage_index) * step_size);
            this->integrand->evaluate(this->y, this->f, control, needSTM);
        }

        void ExplicitRungeKutta::stageLoop(const math::Matrix<doubleType> & state_left,
            math::Matrix<doubleType> & state_right,
            const math::Matrix<double> & STM_left,
            math::Matrix<double> & STM_right,
            const doubleType & step_size)
        {
            for (size_t stage_index = 0; stage_index < this->num_stages - 1; ++stage_index)
            {
                this->evaluateIntegrand(state_left, stage_index, step_size, true);
                this->stateUpdateFromTableauRow(state_left, stage_index + 1, stage_index, step_size);
                this->stmUpdateFromTableauRow(STM_left, stage_index + 1, stage_index, step_size);
            }
            this->evaluateIntegrand(state_left, this->num_stages - 1, step_size, true);
            this->stateUpdateFromWeights(state_left, this->bUpper, this->num_stages - 1, step_size);
            this->stmUpdateFromWeights(STM_left, this->bUpper, this->num_stages - 1, step_size);
        }

        void ExplicitRungeKutta::stageLoop(const math::Matrix<doubleType> & state_left,
            math::Matrix<doubleType> & state_right,
            const math::Matrix<double> & STM_left,
            math::Matrix<double> & STM_right,
            const math::Matrix <doubleType> & control,
            const doubleType & step_size)
        {
            for (size_t stage_index = 0; stage_index < this->num_stages - 1; ++stage_index)
            {
                this->evaluateIntegrand(state_left, control, stage_index, step_size, true);
                this->stateUpdateFromTableauRow(state_left, stage_index + 1, stage_index, step_size);
                this->stmUpdateFromTableauRow(STM_left, stage_index + 1, stage_index, step_size);
            }
            this->evaluateIntegrand(state_left, control, this->num_stages - 1, step_size, true);
            this->stateUpdateFromWeights(state_left, this->bUpper, this->num_stages - 1, step_size);
            this->stmUpdateFromWeights(STM_left, this->bUpper, this->num_stages - 1, step_size);
        }

        void ExplicitRungeKutta::stageLoop(const math::Matrix<doubleType> & state_left,
            math::Matrix<doubleType> & state_right,
            const doubleType & step_size)
        {
            for (size_t stage_index = 0; stage_index < this->num_stages - 1; ++stage_index)
            {
                this->evaluateIntegrand(state_left, stage_index, step_size, false);
                this->stateUpdateFromTableauRow(state_left, stage_index + 1, stage_index, step_size);
            }
            this->evaluateIntegrand(state_left, this->num_stages - 1, step_size, false);
            this->stateUpdateFromWeights(state_left, this->bUpper, this->num_stages - 1, step_size);
        }

        void ExplicitRungeKutta::stageLoop(const math::Matrix<doubleType> & state_left,
            math::Matrix<doubleType> & state_right,
            const math::Matrix <doubleType> & control,
            const doubleType & step_size)
        {
            for (size_t stage_index = 0; stage_index < this->num_stages - 1; ++stage_index)
            {
                this->evaluateIntegrand(state_left, control, stage_index, step_size, false);
                this->stateUpdateFromTableauRow(state_left, stage_index + 1, stage_index, step_size);
            }
            this->evaluateIntegrand(state_left, control, this->num_stages - 1, step_size, false);
            this->stateUpdateFromWeights(state_left, this->bUpper, this->num_stages - 1, step_size);
        }

        void ExplicitRungeKutta::step(const math::Matrix<doubleType> & state_left,
            const math::Matrix<double> & STM_left,
            math::Matrix<doubleType> & state_right,
            math::Matrix<double> & STM_right,
            const doubleType & step_size,
            const double & dstep_sizedProp_var,
            const bool & needSTM)

        {
            this->dstep_sizedProp_var = dstep_sizedProp_var;

            this->y.shallow_copy(state_left);

            if (needSTM)
            {
                this->STM.shallow_copy(STM_left);
                this->stageLoop(state_left, state_right, STM_left, STM_right, step_size);
                STM_right.shallow_copy(this->STM);
            }
            else
            {
                this->stageLoop(state_left, state_right, step_size);
            }
            state_right.shallow_copy(this->y);
        }

        void ExplicitRungeKutta::step(const math::Matrix<doubleType> & state_left,
                                      const math::Matrix<double> & STM_left,
                                      math::Matrix<doubleType> & state_right,
                                      math::Matrix<double> & STM_right,
                                      const math::Matrix <doubleType> & control,
                                      const doubleType & step_size,
                                      const double & dstep_sizedProp_var,
                                      const bool & needSTM)
        {
            this->dstep_sizedProp_var = dstep_sizedProp_var;

            this->y.shallow_copy(state_left);

            if (needSTM)
            {
                this->STM.shallow_copy(STM_left);
                this->stageLoop(state_left, state_right, STM_left, STM_right, control, step_size);
                STM_right.shallow_copy(this->STM);
            }
            else
            {
                this->stageLoop(state_left, state_right, control, step_size);
            }
            state_right.shallow_copy(this->y);
        }

        void ExplicitRungeKutta::computeError(doubleType & error, math::Matrix<double> & error_scaling_factors)
        {
            error = 0.0;
            this->last_error_estimate = EmbeddedErrorEstimate();
            doubleType current_error = 0.0;
            doubleType state_error = 0.0;

            if (!this->RK_tableau->getHasVariableStepCoefficients())
            {
                return;
            }

            for (size_t k = 0; k < this->num_states; ++k)
            {
                state_error = 0.0;
                for (size_t stage_index = 0; stage_index < this->num_stages; ++stage_index)
                {
                    state_error += this->gradient_bin(k, stage_index)
                        * (this->bUpper(stage_index) - this->bLower(stage_index))
                        * this->error_step_size;
                }

                if (this->has_adaptive_error_control_settings)
                {
                    const double left_value = (*this->error_state_left)(k) _GETVALUE;
                    const double right_value = this->y(k) _GETVALUE;
                    const double denominator = this->adaptive_error_control_settings.absolute_tolerances[k]
                        + this->adaptive_error_control_settings.relative_tolerance
                        * std::max(std::fabs(left_value), std::fabs(right_value));
                    current_error = fabs(state_error) / denominator;
                }
                else
                {
                    current_error = fabs(state_error) * error_scaling_factors(k);
                }
                if (current_error > error)
                {
                    error = current_error;
                }
                if ((current_error _GETVALUE) > this->last_error_estimate.state_normalized_error)
                {
                    this->last_error_estimate.state_normalized_error = current_error _GETVALUE;
                    this->last_error_estimate.worst_state_index = k;
                }
            }

            if (this->error_step_includes_STM
                && (!this->has_adaptive_error_control_settings
                    || this->adaptive_error_control_settings.stm_policy == STMErrorControlPolicy::state_and_stm))
            {
                size_t scaling_index = this->num_states;

                for (size_t row = 0; row < this->STM_size; ++row)
                {
                    for (size_t column = 0; column < this->STM_size; ++column)
                    {
                        double STM_error = 0.0;
                        for (size_t stage_index = 0; stage_index < this->num_stages; ++stage_index)
                        {
                            STM_error += this->STM_bin[stage_index](row, column)
                                * (this->bUpper(stage_index) - this->bLower(stage_index));
                        }

                        if (this->has_adaptive_error_control_settings)
                        {
                            const double left_value = (*this->error_stm_left)(row, column);
                            const double right_value = this->STM(row, column);
                            const double denominator = this->adaptive_error_control_settings.stm_absolute_tolerances[scaling_index - this->num_states]
                                + this->adaptive_error_control_settings.stm_relative_tolerance
                                * std::max(std::fabs(left_value), std::fabs(right_value));
                            current_error = fabs(STM_error) / denominator;
                            ++scaling_index;
                        }
                        else
                        {
                            current_error = fabs(STM_error) * error_scaling_factors(scaling_index++);
                        }
                        if (current_error > error)
                        {
                            error = current_error;
                        }
                        if ((current_error _GETVALUE) > this->last_error_estimate.stm_normalized_error)
                        {
                            this->last_error_estimate.stm_normalized_error = current_error _GETVALUE;
                            this->last_error_estimate.worst_stm_row = row;
                            this->last_error_estimate.worst_stm_column = column;
                        }
                    }
                }
            }

            this->last_error_estimate.combined_normalized_error = error _GETVALUE;
        }

        void ExplicitRungeKutta::errorControlledStep(const math::Matrix<doubleType> & state_left,
                                                     const math::Matrix<double> & STM_left,
                                                     math::Matrix<doubleType> & state_right,
                                                     math::Matrix<double> & STM_right,
                                                     const math::Matrix <doubleType> & control,
                                                     const doubleType & step_size,
                                                     const double & dstep_sizedProp_var,
                                                     const bool & needSTM,
                                                     doubleType & error,
                                                     math::Matrix<double> & error_scaling_factors)
        {
            this->dstep_sizedProp_var = dstep_sizedProp_var;
            this->error_step_size = step_size;
            this->error_step_includes_STM = needSTM;
            this->error_state_left = &state_left;
            this->error_stm_left = &STM_left;
            
            this->y.shallow_copy(state_left);

            if (needSTM)
            {
                this->STM.shallow_copy(STM_left);
                this->stageLoop(state_left, state_right, STM_left, STM_right, control, step_size);
                STM_right.shallow_copy(this->STM);
            }
            else
            {
                this->stageLoop(state_left, state_right, control, step_size);
            }

            // TODO: compute bLower stage 

            // compute the relative error between the lower and higher order solutions for this RK step
            this->computeError(error, error_scaling_factors);
            
            state_right.shallow_copy(this->y);
        }

    } // end integration namespace
} // end EMTG namespace
