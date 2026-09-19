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

#include "IntegratedAdaptiveStepPropagator.h"

#include <cmath>
#include <algorithm>
#include <limits>
#include <stdexcept>

namespace EMTG {
    namespace Astrodynamics {

        // constructors

        IntegratedAdaptiveStepPropagator::IntegratedAdaptiveStepPropagator(const size_t & numStates_in,
                                                                           const size_t & STM_size_in) :
                                                                           IntegratedPropagator(numStates_in,
                                                                                                STM_size_in)

        {
            this->integrator_tolerance = 1.0e-8;
            this->uses_normalized_error_control = false;
            this->initial_step_size = 0.0;
            this->minimum_step_size = 0.0;
            this->controller_safety_factor = 0.9;
            this->minimum_step_scale = 0.2;
            this->maximum_step_scale = 5.0;
            this->rejection_limit = 50;
        }

        void IntegratedAdaptiveStepPropagator::setAdaptiveErrorControlSettings(
            const Integration::AdaptiveErrorControlSettings& settings)
        {
            this->integration_scheme->setAdaptiveErrorControlSettings(settings);
            this->uses_normalized_error_control = true;
        }

        void IntegratedAdaptiveStepPropagator::setRequestedEpochs(const std::vector<double>& requested_epochs_in)
        {
            for (const double epoch : requested_epochs_in)
                if (!std::isfinite(epoch))
                    throw std::invalid_argument("Every requested dense-output epoch must be finite.");
            this->requested_epochs = requested_epochs_in;
            this->requested_epoch_emitted.assign(this->requested_epochs.size(), false);
            this->requested_epoch_states.clear();
        }

        void IntegratedAdaptiveStepPropagator::clearRequestedEpochs()
        {
            this->requested_epochs.clear();
            this->requested_epoch_emitted.clear();
            this->requested_epoch_states.clear();
        }

        void IntegratedAdaptiveStepPropagator::setScalarEvent(
            const std::function<double(const math::Matrix<doubleType>&, double)>& event_function,
            const int direction,
            const bool terminal,
            const double root_tolerance)
        {
            if (!event_function)
                throw std::invalid_argument("Scalar integration event function is empty.");
            if (direction < -1 || direction > 1)
                throw std::invalid_argument("Scalar integration event direction must be -1, 0, or +1.");
            if (!std::isfinite(root_tolerance) || root_tolerance <= 0.0)
                throw std::invalid_argument("Scalar integration event root tolerance must be finite and strictly positive.");
            this->scalar_event_function = event_function;
            this->scalar_event_direction = direction;
            this->scalar_event_terminal = terminal;
            this->scalar_event_root_tolerance = root_tolerance;
            this->located_events.clear();
        }

        void IntegratedAdaptiveStepPropagator::clearScalarEvent()
        {
            this->scalar_event_function = nullptr;
            this->located_events.clear();
        }

        // methods
        void IntegratedAdaptiveStepPropagator::propagate(const doubleType & propagation_span, const bool & STM_needed)
        {
            //TODO: probably won't have to do this...just call an appropriately overloaded IntegrationScheme step method
            EMTG::math::Matrix<doubleType> zero_control(4, 1, 0.0);
            this->propagate(propagation_span, zero_control, STM_needed);
        }

        void IntegratedAdaptiveStepPropagator::propagate(const doubleType & propagation_span, const math::Matrix <doubleType> & control, const bool & STM_needed)
        {
            math::Matrix<doubleType> & state_left = *this->StateLeftPointer;
            math::Matrix<doubleType> & state_right = *this->StateRightPointer;
            math::Matrix<double> & STM_ptr = *this->STMpointer;

            this->beginStatistics(propagation_span _GETVALUE);
            this->requested_epoch_states.clear();
            this->requested_epoch_emitted.assign(this->requested_epochs.size(), false);
            this->located_events.clear();
            if (STM_needed && (!this->requested_epochs.empty() || this->scalar_event_function))
                throw std::runtime_error("Adaptive cubic-Hermite dense output and event localization do not support STM output.");

            //configure integration scheme pointers
            this->integration_scheme->setLeftHandIndependentVariablePtr(this->current_epoch);

            // unpack the external state pointer into the integrator's augmented state
            this->propagatorSetup(state_left, STM_ptr, STM_needed);

            this->propagateAdaptiveStep(propagation_span, control, STM_needed);

            // If we got here, we made it all of the way through the whole propagation_span
            // pack the augmented state back into the external state pointer
            this->propagatorTeardown(state_left, state_right, STM_ptr, propagation_span);
            this->finishStatistics();
            
            // now compute the partials of the state w.r.t. the propagation variables
            /*
            if (STM_needed)
            {
                this->computePropVarPartials(propagation_span, control, state_left, state_right, STM_left);
            }
            */

        } // end propagate method

        void IntegratedAdaptiveStepPropagator::computePropVarPartials(const doubleType & propagation_span, 
                                                                      const math::Matrix <doubleType> & control, 
                                                                      math::Matrix<doubleType> & state_left,
                                                                      math::Matrix<doubleType> & state_right,
                                                                      math::Matrix<double> & STM_left)
        {/*
                math::Matrix<doubleType> states_perturbed_foward;
                math::Matrix<doubleType> states_perturbed_backward;
                //double central_difference_interval = 6.7e-05;
                double central_difference_interval = 10.0;
                const double one_over_two_step = 1.0 / (2.0 * central_difference_interval);

                // store the original value of the independent variable
                doubleType original_independent_variable = this->current_epoch;

                // reset the left hand state container
                // also ensure that STM_needed is set to false so that we don't needlessly compute STM entries
                // during the finite differencing
                this->unpackStates(state_left, STM_left, false);

                // forward perturb the current independent variable
                this->current_epoch += central_difference_interval;
                this->propagateAdaptiveStep(propagation_span, control, false);
                states_perturbed_foward = this->state_right;

                // backward perturb the current independent variable
                this->unpackStates(state_left, STM_left, false);
                this->current_epoch = original_independent_variable - central_difference_interval;
                this->propagateAdaptiveStep(propagation_span, control, false);
                states_perturbed_backward = this->state_right;

                for (size_t k = 0; k < this->STM_size; ++k)
                {
                    dstate_rightdProp_vars(k, 0) = ((states_perturbed_foward(k) - states_perturbed_backward(k)) * one_over_two_step) _GETVALUE;
                }

                // clean up the perturbation of the current independent variable
                this->current_epoch = original_independent_variable;

                // forward perturb the propagation span
                this->unpackStates(state_left, STM_left, false);
                this->propagateAdaptiveStep(propagation_span + central_difference_interval, control, false);
                states_perturbed_foward = this->state_right;

                // backward perturb the propagation span
                this->unpackStates(state_left, STM_left, false);
                this->propagateAdaptiveStep(propagation_span - central_difference_interval, control, false);
                states_perturbed_backward = this->state_right;


                for (size_t k = 0; k < this->STM_size; ++k)
                {
                    dstate_rightdProp_vars(k, 1) = ((states_perturbed_foward(k) - states_perturbed_backward(k)) * one_over_two_step) _GETVALUE;

                    // the propagator is only aware of the local propagation span that is passed to it
                    // therefore we must divide by the propagation variable modifier (e.g. 1/N for Sims-Flanagan and FBLT)
                    // difference is (TOF/N + dTOF)    vs.     (TOF + dTOF) / N
                    // if the propagation is backwards, then the sign is flipped
                    dstate_rightdProp_vars(k, 1) *= propagation_span < 0.0 ? -this->boundary_target_dstep_sizedProp_var : this->boundary_target_dstep_sizedProp_var;
                }
            */
        } // end computePropVarPartials method

        math::Matrix<doubleType> IntegratedAdaptiveStepPropagator::evaluateCubicHermiteState(
            const math::Matrix<doubleType>& state_left,
            const math::Matrix<doubleType>& state_right,
            const math::Matrix<doubleType>& derivative_left,
            const math::Matrix<doubleType>& derivative_right,
            const double step_size,
            const double theta) const
        {
            const double theta2 = theta * theta;
            const double theta3 = theta2 * theta;
            const double h00 = 2.0 * theta3 - 3.0 * theta2 + 1.0;
            const double h10 = theta3 - 2.0 * theta2 + theta;
            const double h01 = -2.0 * theta3 + 3.0 * theta2;
            const double h11 = theta3 - theta2;
            math::Matrix<doubleType> interpolated(this->numStates, 1, 0.0);
            for (size_t state_index = 0; state_index < this->numStates; ++state_index)
            {
                interpolated(state_index) = h00 * state_left(state_index)
                    + h10 * step_size * derivative_left(state_index)
                    + h01 * state_right(state_index)
                    + h11 * step_size * derivative_right(state_index);
            }
            return interpolated;
        }

        bool IntegratedAdaptiveStepPropagator::processDenseOutputAndEvents(
            const math::Matrix<doubleType>& accepted_state_left,
            math::Matrix<doubleType>& accepted_state_right,
            const math::Matrix<doubleType>& control,
            const double independent_variable_left,
            double& accepted_step,
            bool& event_detected)
        {
            event_detected = false;
            bool has_requested_epoch_in_step = false;
            for (size_t request_index = 0; request_index < this->requested_epochs.size(); ++request_index)
            {
                if (this->requested_epoch_emitted[request_index])
                    continue;
                const double theta = (this->requested_epochs[request_index] - independent_variable_left) / accepted_step;
                if (theta >= 0.0 && theta <= 1.0)
                {
                    has_requested_epoch_in_step = true;
                    break;
                }
            }
            if (!has_requested_epoch_in_step && !this->scalar_event_function)
                return false;

            const double full_step = accepted_step;
            const double independent_variable_right = independent_variable_left + full_step;
            const math::Matrix<doubleType> full_state_right = accepted_state_right;
            math::Matrix<doubleType> derivative_left(this->numStates, 1, 0.0);
            math::Matrix<doubleType> derivative_right(this->numStates, 1, 0.0);
            this->integrand->setCurrentIndependentVariable(independent_variable_left);
            this->integrand->evaluate(accepted_state_left, derivative_left, control, false);
            this->integrand->setCurrentIndependentVariable(independent_variable_right);
            this->integrand->evaluate(full_state_right, derivative_right, control, false);
            this->integration_statistics.rhs_evaluations += 2;

            double output_theta_limit = 1.0;
            bool terminal_event = false;
            if (this->scalar_event_function)
            {
                const double value_left = this->scalar_event_function(accepted_state_left, independent_variable_left);
                const double value_right = this->scalar_event_function(full_state_right, independent_variable_right);
                if (!std::isfinite(value_left) || !std::isfinite(value_right))
                    throw std::runtime_error("Scalar integration event returned NaN or infinity.");

                const bool upward_crossing = value_left < 0.0 && value_right >= 0.0;
                const bool downward_crossing = value_left > 0.0 && value_right <= 0.0;
                const bool direction_matches = this->scalar_event_direction == 0
                    ? upward_crossing || downward_crossing
                    : (this->scalar_event_direction > 0 ? upward_crossing : downward_crossing);
                if (direction_matches)
                {
                    double theta_left = 0.0;
                    double theta_right = 1.0;
                    double bracket_value_left = value_left;
                    math::Matrix<doubleType> root_state = full_state_right;
                    while (std::fabs((theta_right - theta_left) * full_step) > this->scalar_event_root_tolerance)
                    {
                        const double theta_mid = 0.5 * (theta_left + theta_right);
                        math::Matrix<doubleType> state_mid = this->evaluateCubicHermiteState(
                            accepted_state_left, full_state_right, derivative_left, derivative_right, full_step, theta_mid);
                        const double value_mid = this->scalar_event_function(
                            state_mid, independent_variable_left + theta_mid * full_step);
                        if (!std::isfinite(value_mid))
                            throw std::runtime_error("Scalar integration event returned NaN or infinity during root localization.");
                        if ((bracket_value_left < 0.0 && value_mid >= 0.0)
                            || (bracket_value_left > 0.0 && value_mid <= 0.0))
                        {
                            theta_right = theta_mid;
                            root_state = state_mid;
                        }
                        else
                        {
                            theta_left = theta_mid;
                            bracket_value_left = value_mid;
                        }
                    }

                    const double root_theta = 0.5 * (theta_left + theta_right);
                    root_state = this->evaluateCubicHermiteState(
                        accepted_state_left, full_state_right, derivative_left, derivative_right, full_step, root_theta);
                    LocatedIntegrationEvent event;
                    event.independent_variable = independent_variable_left + root_theta * full_step;
                    event.event_value = this->scalar_event_function(root_state, event.independent_variable);
                    event.terminal = this->scalar_event_terminal;
                    event.state = root_state;
                    this->located_events.push_back(event);
                    ++this->integration_statistics.event_landings;
                    event_detected = true;

                    if (this->scalar_event_terminal)
                    {
                        accepted_state_right = root_state;
                        accepted_step = root_theta * full_step;
                        output_theta_limit = root_theta;
                        terminal_event = true;
                    }
                }
            }

            for (size_t request_index = 0; request_index < this->requested_epochs.size(); ++request_index)
            {
                if (this->requested_epoch_emitted[request_index])
                    continue;
                const double theta = (this->requested_epochs[request_index] - independent_variable_left) / full_step;
                if (theta < 0.0 || theta > output_theta_limit)
                    continue;

                DenseOutputPoint point;
                point.independent_variable = this->requested_epochs[request_index];
                if (theta == 0.0)
                    point.state = accepted_state_left;
                else if (theta == output_theta_limit && terminal_event)
                    point.state = full_state_right;
                else if (theta == 1.0)
                    point.state = accepted_state_right;
                else
                    point.state = this->evaluateCubicHermiteState(
                        accepted_state_left, full_state_right, derivative_left, derivative_right, full_step, theta);
                this->requested_epoch_states.push_back(point);
                this->requested_epoch_emitted[request_index] = true;
                ++this->integration_statistics.requested_epoch_evaluations;
            }

            return terminal_event;
        }

        void IntegratedAdaptiveStepPropagator::propagateAdaptiveStep(const doubleType & propagation_span,
                                                                     const math::Matrix <doubleType> & control,
                                                                     const bool & STM_needed)
        {


            const double span = propagation_span _GETVALUE;
            if (!std::isfinite(span))
                throw std::runtime_error("Adaptive propagation span is NaN or infinite.");

            if (span == 0.0)
            {
                return;
            }

            const auto positive_finite = [](const double value)
            {
                return std::isfinite(value) && value > 0.0;
            };
            if (!positive_finite(this->PropagationStepSize))
                throw std::runtime_error("Adaptive maximum step size must be finite and strictly positive.");
            if (this->initial_step_size < 0.0 || !std::isfinite(this->initial_step_size))
                throw std::runtime_error("Adaptive initial step size must be finite and non-negative.");
            if (this->minimum_step_size < 0.0 || !std::isfinite(this->minimum_step_size))
                throw std::runtime_error("Adaptive minimum step size must be finite and non-negative.");
            if (!positive_finite(this->controller_safety_factor)
                || this->controller_safety_factor > 1.0
                || !positive_finite(this->minimum_step_scale)
                || !positive_finite(this->maximum_step_scale)
                || this->minimum_step_scale > 1.0
                || this->maximum_step_scale < 1.0
                || this->rejection_limit == 0)
                throw std::runtime_error("Adaptive controller parameters are invalid.");

            const double direction = span > 0.0 ? 1.0 : -1.0;
            const double maximum_step = this->PropagationStepSize;
            const double configured_initial_step = this->initial_step_size > 0.0
                ? this->initial_step_size
                : maximum_step;
            const double independent_scale = std::max({1.0,
                                                       std::fabs(this->current_independent_variable _GETVALUE),
                                                       std::fabs(span)});
            const double automatic_minimum = 64.0 * std::numeric_limits<double>::epsilon() * independent_scale;
            const double minimum_step = this->minimum_step_size > 0.0
                ? this->minimum_step_size
                : automatic_minimum;
            const double acceptance_limit = this->uses_normalized_error_control
                ? 1.0
                : this->integrator_tolerance;

            if (!positive_finite(acceptance_limit))
                throw std::runtime_error("Adaptive error acceptance limit must be finite and strictly positive.");

            double accumulated = 0.0;
            double next_step = direction * std::min({configured_initial_step, maximum_step, std::fabs(span)});

            while (accumulated != span)
            {
                const double remaining = span - accumulated;
                const bool endpoint_capped = std::fabs(next_step) >= std::fabs(remaining);
                double trial_step = endpoint_capped ? remaining : next_step;
                size_t consecutive_rejections = 0;
                double normalized_error = 0.0;

                while (true)
                {
                    if (!std::isfinite(trial_step)
                        || trial_step == 0.0
                        || (std::fabs(trial_step) < minimum_step && std::fabs(remaining) > minimum_step))
                    {
                        ++this->integration_statistics.underflow_failures;
                        throw std::runtime_error("Adaptive step underflow: the proposed step cannot advance the independent variable reliably.");
                    }

                    const bool landing_trial = std::fabs(trial_step) >= std::fabs(remaining);
                    trial_step = landing_trial ? remaining : trial_step;
                    this->dstep_sizedProp_var = landing_trial
                        ? direction * this->boundary_target_dstep_sizedProp_var
                        : 0.0;

                    doubleType adaptive_step_error = 0.0;
                    ++this->integration_statistics.attempted_steps;
                    this->integration_scheme->errorControlledStep(this->state_left,
                                                                  this->STM_left,
                                                                  this->state_right,
                                                                  this->STM_right,
                                                                  control,
                                                                  trial_step,
                                                                  this->dstep_sizedProp_var,
                                                                  STM_needed,
                                                                  adaptive_step_error,
                                                                  this->error_scaling_factors);
                    this->integration_statistics.rhs_evaluations += this->integration_scheme->getLastStepRhsEvaluations();
                    if (STM_needed)
                        this->integration_statistics.stm_rhs_evaluations += this->integration_scheme->getLastStepRhsEvaluations();

                    normalized_error = (adaptive_step_error _GETVALUE) / acceptance_limit;
                    if (!std::isfinite(normalized_error))
                        throw std::runtime_error("Adaptive embedded error estimate is NaN or infinite.");
                    for (size_t state_index = 0; state_index < this->numStates; ++state_index)
                        if (!std::isfinite(this->state_right(state_index) _GETVALUE))
                            throw std::runtime_error("Adaptive trial state contains NaN or infinity.");
                    if (STM_needed)
                        for (size_t row = 0; row < this->STM_size; ++row)
                            for (size_t column = 0; column < this->STM_size; ++column)
                                if (!std::isfinite(this->STM_right(row, column)))
                                    throw std::runtime_error("Adaptive trial STM contains NaN or infinity.");

                    this->integration_statistics.maximum_normalized_error =
                        std::max(this->integration_statistics.maximum_normalized_error, normalized_error);
                    this->integration_statistics.final_normalized_error = normalized_error;

                    if (normalized_error <= 1.0)
                        break;

                    ++this->integration_statistics.rejected_steps;
                    if (++consecutive_rejections > this->rejection_limit)
                    {
                        ++this->integration_statistics.rejection_limit_failures;
                        throw std::runtime_error("Adaptive step rejection limit exceeded.");
                    }

                    const double factor = std::max(this->minimum_step_scale,
                        std::min(1.0, this->controller_safety_factor * std::pow(normalized_error, -1.0 / 8.0)));
                    trial_step *= factor;
                }

                bool event_detected = false;
                const bool terminal_event = this->processDenseOutputAndEvents(
                    this->state_left,
                    this->state_right,
                    control,
                    this->current_independent_variable _GETVALUE,
                    trial_step,
                    event_detected);

                if (this->store_propagation_history)
                {
                    if (this->index_of_epoch_in_state_vec < this->state_left.get_n())
                        this->propagation_history.push_back(
                            this->state_right(this->index_of_epoch_in_state_vec) _GETVALUE
                            - this->state_left(this->index_of_epoch_in_state_vec) _GETVALUE);
                    else
                        this->propagation_history.push_back(trial_step);
                }

                this->state_left = this->state_right;
                this->STM_left = this->STM_right;
                accumulated += trial_step;
                if (std::fabs(span - accumulated) <= 4.0 * std::numeric_limits<double>::epsilon() * std::max(1.0, std::fabs(span)))
                    accumulated = span;

                this->current_independent_variable += trial_step;
                if (this->index_of_epoch_in_state_vec < this->state_left.get_n())
                    this->current_epoch = this->state_left(this->index_of_epoch_in_state_vec);
                else
                    this->current_epoch += trial_step;

                ++this->integration_statistics.accepted_steps;
                const double accepted_magnitude = std::fabs(trial_step);
                this->integration_statistics.minimum_accepted_step =
                    std::min(this->integration_statistics.minimum_accepted_step, accepted_magnitude);
                this->integration_statistics.maximum_accepted_step =
                    std::max(this->integration_statistics.maximum_accepted_step, accepted_magnitude);
                this->integration_statistics.accumulated_accepted_step += accepted_magnitude;
                if (endpoint_capped)
                    ++this->integration_statistics.endpoint_capped_steps;

                if (terminal_event)
                {
                    this->integration_statistics.propagation_span = accumulated;
                    break;
                }

                if (accumulated == span)
                    break;

                const double growth = normalized_error == 0.0
                    ? this->maximum_step_scale
                    : std::max(this->minimum_step_scale,
                        std::min(this->maximum_step_scale,
                                 this->controller_safety_factor * std::pow(normalized_error, -1.0 / 8.0)));
                const double proposed_magnitude = event_detected
                    ? std::min(maximum_step, configured_initial_step)
                    : std::min(maximum_step, accepted_magnitude * growth);
                next_step = direction * proposed_magnitude;
                const double new_remaining = span - accumulated;
                if (std::fabs(next_step) > std::fabs(new_remaining))
                    next_step = new_remaining;
            }
        } // end propagateAdaptiveStep method


    } // end namespace Astrodynamics
} // end namespace EMTG
