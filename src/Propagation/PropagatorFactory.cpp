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

//propagator factory
//Jacob Englander 1-2-2018

#include "PropagatorFactory.h"

#include "IntegratedAdaptiveStepPropagator.h"
#include "IntegratedFixedStepPropagator.h"
#include "KeplerPropagatorTimeDomain.h"

#include <algorithm>
#include <exception>
#include <vector>

namespace EMTG
{
    namespace Astrodynamics
    {
        //for non-integrators
        PropagatorBase* CreatePropagator(missionoptions* myOptions,
                                         Astrodynamics::universe* myUniverse,
                                         const size_t& num_states,
                                         math::Matrix <doubleType> & StateLeft,
                                         math::Matrix <doubleType> & StateRight,
                                         math::Matrix <double> & STM,
                                         math::Matrix <double> & dStatedIndependentVariable,
                                         double* dPropagationTime_dIndependentVariable)
        {
            KeplerPropagatorTimeDomain* myPropagator = new KeplerPropagatorTimeDomain(num_states);
            myPropagator->setStateLeft(StateLeft);
            myPropagator->setStateRight(StateRight);
            myPropagator->setSTM(STM);
            myPropagator->setdStatedIndependentVariable(dStatedIndependentVariable);
            myPropagator->set_dPropagationTime_dIndependentVariable(dPropagationTime_dIndependentVariable);
            myPropagator->setCentralBodyGM(myUniverse->mu);

            return myPropagator;
        }

        //for integrators
        PropagatorBase* CreatePropagator(missionoptions* myOptions,
                                         Astrodynamics::universe* myUniverse,
                                         const size_t & numStates_in, 
                                         const size_t & STM_size_in,                                          
                                         math::Matrix <doubleType> & StateLeft,
                                         math::Matrix <doubleType> & StateRight,
                                         math::Matrix <double> & STM,
                                         math::Matrix <double> & dStatedIndependentVariable,
                                         Integration::Integrand * Integrand,
                                         Integration::IntegrationScheme * IntegrationScheme,
                                         double* BoundaryTarget_dStepSizePropVar,
                                         const double PropagationStepSize)
        {
            if (myOptions->integratorType == IntegratorType::rk8_fixed)
            {
                IntegratedFixedStepPropagator* myPropagator = new IntegratedFixedStepPropagator(numStates_in, STM_size_in);
                myPropagator->setStateLeft(StateLeft);
                myPropagator->setStateRight(StateRight);
                myPropagator->setSTM(STM);
                myPropagator->setdStatedIndependentVariable(dStatedIndependentVariable);
                myPropagator->setIntegrand(Integrand);
                myPropagator->setIntegrationScheme(IntegrationScheme);
                myPropagator->setBoundaryTarget_dStepSizedPropVar(BoundaryTarget_dStepSizePropVar);
                myPropagator->setPropagationStepSize(PropagationStepSize);


                return myPropagator;
            }
            else if (myOptions->integratorType == IntegratorType::rk7813m_adaptive)
            {
                IntegratedAdaptiveStepPropagator* myPropagator = new IntegratedAdaptiveStepPropagator(numStates_in, STM_size_in);
                myPropagator->setStateLeft(StateLeft);
                myPropagator->setStateRight(StateRight);
                myPropagator->setSTM(STM);
                myPropagator->setdStatedIndependentVariable(dStatedIndependentVariable);
                myPropagator->setIntegrand(Integrand);
                myPropagator->setIntegrationScheme(IntegrationScheme);
                myPropagator->setBoundaryTarget_dStepSizedPropVar(BoundaryTarget_dStepSizePropVar);
                myPropagator->setPropagationStepSize(PropagationStepSize);
                myPropagator->setTolerance(myOptions->integrator_tolerance);
                myPropagator->setInitialStepSize(myOptions->integrator_initial_step_size);
                myPropagator->setMinimumStepSize(myOptions->integrator_minimum_step_size);
                myPropagator->setControllerSafetyFactor(myOptions->integrator_safety_factor);
                myPropagator->setMinimumStepScale(myOptions->integrator_minimum_step_scale);
                myPropagator->setMaximumStepScale(myOptions->integrator_maximum_step_scale);
                myPropagator->setRejectionLimit(myOptions->integrator_rejection_limit);

                // Characteristic scales encode the units of each integrated state.
                // EMTG's currently integrated formulations share Cartesian position
                // and velocity in slots 0..5, mass in slot 6, epoch in slot 7,
                // virtual propellant in slots 8..9, and optional control/propagation
                // variable columns after the physical-state columns.
                std::vector<double> state_scales(numStates_in, 1.0);
                for (size_t state_index = 0; state_index < numStates_in; ++state_index)
                {
                    if (state_index < 3)
                        state_scales[state_index] = myUniverse->LU;
                    else if (state_index < 6)
                        state_scales[state_index] = myUniverse->LU / myUniverse->TU;
                    else if (state_index == 6 || state_index >= 8)
                        state_scales[state_index] = myOptions->maximum_mass;
                    else if (state_index == 7)
                        state_scales[state_index] = myUniverse->TU;
                }

                std::vector<double> stm_coordinate_scales(STM_size_in, 1.0);
                for (size_t coordinate = 0; coordinate < std::min(numStates_in, STM_size_in); ++coordinate)
                    stm_coordinate_scales[coordinate] = state_scales[coordinate];
                if (STM_size_in > numStates_in)
                    stm_coordinate_scales.back() = myUniverse->TU; // propagation-variable column

                Integration::AdaptiveErrorControlSettings settings;
                settings.relative_tolerance = myOptions->integrator_error_control_mode == 0
                    ? myOptions->integrator_tolerance
                    : myOptions->integrator_relative_tolerance;
                settings.absolute_tolerances.resize(numStates_in);
                for (size_t state_index = 0; state_index < numStates_in; ++state_index)
                {
                    if (myOptions->integrator_error_control_mode == 0)
                    {
                        // Deterministic migration for legacy files: the old scalar
                        // tolerance becomes rtol and also scales each dimensional atol.
                        settings.absolute_tolerances[state_index] =
                            myOptions->integrator_tolerance * state_scales[state_index];
                    }
                    else if (state_index < 3)
                        settings.absolute_tolerances[state_index] = myOptions->integrator_absolute_tolerance_position;
                    else if (state_index < 6)
                        settings.absolute_tolerances[state_index] = myOptions->integrator_absolute_tolerance_velocity;
                    else if (state_index == 7)
                        settings.absolute_tolerances[state_index] = myOptions->integrator_absolute_tolerance_time;
                    else if (state_index == 6 || state_index == 8 || state_index == 9)
                        settings.absolute_tolerances[state_index] = myOptions->integrator_absolute_tolerance_mass;
                    else
                        settings.absolute_tolerances[state_index] = myOptions->integrator_absolute_tolerance_other;
                }

                settings.stm_relative_tolerance = myOptions->integrator_error_control_mode == 0
                    ? myOptions->integrator_tolerance
                    : myOptions->integrator_stm_relative_tolerance;
                settings.stm_policy = myOptions->integrator_stm_error_control == 0
                    ? Integration::STMErrorControlPolicy::state_only
                    : Integration::STMErrorControlPolicy::state_and_stm;
                settings.stm_absolute_tolerances.resize(STM_size_in * STM_size_in);
                for (size_t row = 0; row < STM_size_in; ++row)
                    for (size_t column = 0; column < STM_size_in; ++column)
                    {
                        const double dimension_scale = stm_coordinate_scales[row] / stm_coordinate_scales[column];
                        const double base_tolerance = myOptions->integrator_error_control_mode == 0
                            ? myOptions->integrator_tolerance
                            : myOptions->integrator_stm_absolute_tolerance;
                        settings.stm_absolute_tolerances[row * STM_size_in + column] = base_tolerance * dimension_scale;
                    }

                myPropagator->setAdaptiveErrorControlSettings(settings);

                // Retained only for source compatibility with direct legacy users;
                // normalized component error control above is the production path.
                math::Matrix<double> error_scaling_factors(numStates_in + STM_size_in * STM_size_in, 1, 1.0);
                myPropagator->setErrorScalingFactors(error_scaling_factors);

                return myPropagator;
            }
            else
            {
            throw std::invalid_argument("Integrator type not implemented. Place a breakpoint in " + std::string(__FILE__) + ", line " + std::to_string(__LINE__));
            }

            return NULL;
        }
    }//end namespace HardwareModels
}//end namespace EMTG
