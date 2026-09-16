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

#ifndef INTEGRATED_ADAPTIVE_STEP_PROPAGATOR_H
#define INTEGRATED_ADAPTIVE_STEP_PROPAGATOR_H

#include "IntegratedPropagator.h"

#include <functional>
#include <vector>

namespace EMTG {
    namespace Astrodynamics {

        struct DenseOutputPoint
        {
            double independent_variable = 0.0;
            math::Matrix<doubleType> state;
        };

        struct LocatedIntegrationEvent
        {
            double independent_variable = 0.0;
            double event_value = 0.0;
            bool terminal = false;
            math::Matrix<doubleType> state;
        };

        class IntegratedAdaptiveStepPropagator : public IntegratedPropagator
        {
        public:
            // constructors
            IntegratedAdaptiveStepPropagator(const size_t & numStates_in,
                                             const size_t & STM_size_in);

            //clone
            virtual IntegratedAdaptiveStepPropagator* clone() const { return new IntegratedAdaptiveStepPropagator(*this); }

            // methods
            inline void setBoundaryTarget_dStepSizedPropVar(const double * boundary_target_dstep_sizedProp_var_in)
            {
                this->boundary_target_dstep_sizedProp_var = *boundary_target_dstep_sizedProp_var_in;
            }

            inline void setErrorScalingFactors(math::Matrix<double> & error_scaling_factors_in) { this->error_scaling_factors = error_scaling_factors_in; };
            inline void setTolerance(const double& Tolerance) { this->integrator_tolerance = Tolerance; }
            void setAdaptiveErrorControlSettings(const Integration::AdaptiveErrorControlSettings& settings);
            inline void setInitialStepSize(const double value) { this->initial_step_size = value; }
            inline void setMinimumStepSize(const double value) { this->minimum_step_size = value; }
            inline void setControllerSafetyFactor(const double value) { this->controller_safety_factor = value; }
            inline void setMinimumStepScale(const double value) { this->minimum_step_scale = value; }
            inline void setMaximumStepScale(const double value) { this->maximum_step_scale = value; }
            inline void setRejectionLimit(const size_t value) { this->rejection_limit = value; }
            void setRequestedEpochs(const std::vector<double>& requested_epochs);
            const std::vector<DenseOutputPoint>& getRequestedEpochStates() const { return this->requested_epoch_states; }
            void clearRequestedEpochs();
            void setScalarEvent(const std::function<double(const math::Matrix<doubleType>&, double)>& event_function,
                                const int direction,
                                const bool terminal,
                                const double root_tolerance);
            void clearScalarEvent();
            const std::vector<LocatedIntegrationEvent>& getLocatedEvents() const { return this->located_events; }

            virtual void propagate(const doubleType & propagation_span, const bool & needSTM);
            virtual void propagate(const doubleType & propagation_span, const math::Matrix <doubleType> & control, const bool & needSTM);

            // fields

        private:
            double integrator_tolerance;
            bool uses_normalized_error_control;
            double initial_step_size;
            double minimum_step_size;
            double controller_safety_factor;
            double minimum_step_scale;
            double maximum_step_scale;
            size_t rejection_limit;

            std::vector<double> requested_epochs;
            std::vector<bool> requested_epoch_emitted;
            std::vector<DenseOutputPoint> requested_epoch_states;
            std::function<double(const math::Matrix<doubleType>&, double)> scalar_event_function;
            int scalar_event_direction = 0;
            bool scalar_event_terminal = false;
            double scalar_event_root_tolerance = 1.0e-8;
            std::vector<LocatedIntegrationEvent> located_events;

            double boundary_target_dstep_sizedProp_var;
            math::Matrix<double> error_scaling_factors;

            void propagateAdaptiveStep(const doubleType & propagation_span, 
                                       const math::Matrix <doubleType> & control, 
                                       const bool & STM_needed);

            void computePropVarPartials(const doubleType & propagation_span, 
                                        const math::Matrix <doubleType> & control, 
                                        math::Matrix<doubleType> & state_left,
                                        math::Matrix<doubleType> & state_right,
                                        math::Matrix<double> & STM_left);

            math::Matrix<doubleType> evaluateCubicHermiteState(
                const math::Matrix<doubleType>& state_left,
                const math::Matrix<doubleType>& state_right,
                const math::Matrix<doubleType>& derivative_left,
                const math::Matrix<doubleType>& derivative_right,
                const double step_size,
                const double theta) const;

            bool processDenseOutputAndEvents(const math::Matrix<doubleType>& accepted_state_left,
                                             math::Matrix<doubleType>& accepted_state_right,
                                             const math::Matrix<doubleType>& control,
                                             const double independent_variable_left,
                                             double& accepted_step,
                                             bool& event_detected);

        };

    } // end namespace Astrodynamics
} // end namespace EMTG

#endif
