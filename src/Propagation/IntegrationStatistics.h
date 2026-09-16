// EMTG: Evolutionary Mission Trajectory Generator
// An open-source global optimization tool for preliminary mission design

#pragma once

#include <cstddef>
#include <limits>

namespace EMTG
{
    namespace Astrodynamics
    {
        struct IntegrationStatistics
        {
            size_t attempted_steps = 0;
            size_t accepted_steps = 0;
            size_t rejected_steps = 0;
            size_t rhs_evaluations = 0;
            size_t stm_rhs_evaluations = 0;
            size_t endpoint_capped_steps = 0;
            size_t requested_epoch_evaluations = 0;
            size_t event_landings = 0;
            size_t underflow_failures = 0;
            size_t rejection_limit_failures = 0;
            double minimum_accepted_step = std::numeric_limits<double>::infinity();
            double maximum_accepted_step = 0.0;
            double accumulated_accepted_step = 0.0;
            double maximum_normalized_error = 0.0;
            double final_normalized_error = 0.0;
            double propagation_span = 0.0;
            double wall_clock_seconds = 0.0;

            double meanAcceptedStep() const
            {
                return this->accepted_steps == 0
                    ? 0.0
                    : this->accumulated_accepted_step / static_cast<double>(this->accepted_steps);
            }

            void reset()
            {
                *this = IntegrationStatistics();
            }
        };
    }
}
