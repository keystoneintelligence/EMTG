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

#include "Integrand.h"
#include "IntegrationScheme.h"

#include <cmath>

namespace EMTG {
    namespace Integration {

        void AdaptiveErrorControlSettings::validate(const size_t state_size, const size_t stm_size) const
        {
            const auto positive_finite = [](const double value)
            {
                return std::isfinite(value) && value > 0.0;
            };

            if (!positive_finite(this->relative_tolerance))
                throw std::invalid_argument("Adaptive relative tolerance must be finite and strictly positive.");
            if (this->absolute_tolerances.size() != state_size)
                throw std::invalid_argument("Adaptive absolute-tolerance vector does not match the integrated state size.");
            for (const double tolerance : this->absolute_tolerances)
                if (!positive_finite(tolerance))
                    throw std::invalid_argument("Every adaptive state absolute tolerance must be finite and strictly positive.");

            if (this->stm_policy == STMErrorControlPolicy::state_and_stm)
            {
                if (!positive_finite(this->stm_relative_tolerance))
                    throw std::invalid_argument("Adaptive STM relative tolerance must be finite and strictly positive.");
                if (this->stm_absolute_tolerances.size() != stm_size * stm_size)
                    throw std::invalid_argument("Adaptive STM absolute-tolerance vector does not match the STM size.");
                for (const double tolerance : this->stm_absolute_tolerances)
                    if (!positive_finite(tolerance))
                        throw std::invalid_argument("Every adaptive STM absolute tolerance must be finite and strictly positive.");
            }
        }
        
        IntegrationScheme::IntegrationScheme() {}
        IntegrationScheme::IntegrationScheme(Integrand * integrand_in) : integrand(integrand_in)
        {
        }

        IntegrationScheme::IntegrationScheme(Integrand * integrand_in, 
                                             const size_t & num_states_in, 
                                             const size_t & STM_size_in) : integrand(integrand_in), 
                                             num_states(num_states_in),
                                             STM_size(STM_size_in)
        {
        }
       

    } // end namespace Integration
} // end namespace EMTG
