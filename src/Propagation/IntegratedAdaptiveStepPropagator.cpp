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
#include <stdexcept>

namespace EMTG {
    namespace Astrodynamics {

        // constructors

        IntegratedAdaptiveStepPropagator::IntegratedAdaptiveStepPropagator(const size_t & numStates_in,
                                                                           const size_t & STM_size_in) :
                                                                           IntegratedPropagator(numStates_in,
                                                                                                STM_size_in)

        {

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

            //configure integration scheme pointers
            this->integration_scheme->setLeftHandIndependentVariablePtr(this->current_epoch);

            // unpack the external state pointer into the integrator's augmented state
            this->propagatorSetup(state_left, STM_ptr, STM_needed);

            this->propagateAdaptiveStep(propagation_span, control, STM_needed);

            // If we got here, we made it all of the way through the whole propagation_span
            // pack the augmented state back into the external state pointer
            this->propagatorTeardown(state_left, state_right, STM_ptr, propagation_span);
            
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

        void IntegratedAdaptiveStepPropagator::propagateAdaptiveStep(const doubleType & propagation_span, 
                                                                     const math::Matrix <doubleType> & control,  
                                                                     const bool & STM_needed)
        {


            if (propagation_span == 0.0)
            {
                return;
            }

            doubleType accumulatedH = 0.0;
            doubleType effectiveH = 0.0;
            doubleType nextStep = fabs(this->PropagationStepSize);

            if (nextStep <= 0.0)
            {
                throw std::runtime_error("rk7813M: adaptive step size must be positive. Aborting.");
            }

            if (propagation_span < 0.0)
            {
                nextStep *= -1.0;
            }

            if (fabs(propagation_span) < fabs(nextStep))
            {
                nextStep = propagation_span;
            }

            // TODO: must finite difference for this
            // double deffectiveHdTOF = 0.0;

            doubleType adaptive_step_error = 1.0e-20;

            // loop until we get all the way through the full propagation_span 
            do
            {
                bool step_accepted = false;

                // take a trial step
                do
                { // cycle until the trial RK step achieves sufficient accuracy

                    effectiveH = nextStep;
                    // Take the trial RK step
                    this->integration_scheme->errorControlledStep(this->state_left,
                                                                  this->STM_left,
                                                                  this->state_right,
                                                                  this->STM_right,
                                                                  control,
                                                                  effectiveH,
                                                                  this->dstep_sizedProp_var,
                                                                  STM_needed,
                                                                  adaptive_step_error,
                                                                  this->error_scaling_factors);

                    // no error!  give it a real value so we don't divide by zero.
                    if (adaptive_step_error == 0.0)
                    {
                        adaptive_step_error = 1e-15; //Almost zero!
                    }

                    // if we rejected the sub-step (i.e. the error was too large) shorten the time step
                    if (adaptive_step_error > this->integrator_tolerance)
                    {
                        //effectiveH = 0.98*effectiveH*pow(this->myOptions->integrator_tolerance / adaptive_step_error, 0.17);
                        nextStep = 0.98 * effectiveH * pow(this->integrator_tolerance / adaptive_step_error, 0.17);

                        //if we make the time step too small, kill the integration - h is too small
                        if (fabs(nextStep) < 1e-13)
                        {
                            throw std::runtime_error("rk7813M: H Got too Small. The integrator has Alexed. Aborting.");
                        }
                    }
                    else
                    {
                        step_accepted = true;
                    }

                } while (!step_accepted);

                // if we got here, then the trial substep was accurate enough; it becomes the new left
                if (this->store_propagation_history)
                {
                    this->propagation_history.push_back(this->state_right(this->index_of_epoch_in_state_vec) _GETVALUE - this->state_left(this->index_of_epoch_in_state_vec) _GETVALUE);
                }

                this->state_left = this->state_right;

                this->STM_left = this->STM_right;

                // keep track of our progress through the full RK step
                accumulatedH += effectiveH;

                // move the left hand independent variable for the next substep forward to the correct value
                this->current_independent_variable += effectiveH;
                if (this->index_of_epoch_in_state_vec < this->state_left.get_n())
                {
                    this->current_epoch = this->state_left(this->index_of_epoch_in_state_vec);
                }
                else
                {
                    this->current_epoch += effectiveH;
                }

                if (fabs(propagation_span - accumulatedH) > 0.0)
                {
                    // make the sub-step a bit longer to save computation time
                    nextStep = 1.01 * effectiveH * pow(this->integrator_tolerance / adaptive_step_error, 0.18);

                    // if our next step will push us over, reduce it to hit the target exactly
                    if (fabs(propagation_span - accumulatedH) < fabs(nextStep))
                    {
                        nextStep = propagation_span - accumulatedH;
                    }

                    if (fabs(nextStep) < 1e-13)
                    {
                        throw std::runtime_error("rk7813M: H Got too Small. The integrator has Alexed. Aborting.");
                    }
                }

            } while (fabs(accumulatedH) < fabs(propagation_span));
        } // end propagateAdaptiveStep method


    } // end namespace Astrodynamics
} // end namespace EMTG
