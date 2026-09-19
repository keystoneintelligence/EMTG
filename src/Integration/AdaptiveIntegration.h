// EMTG: Evolutionary Mission Trajectory Generator
// An open-source global optimization tool for preliminary mission design

#pragma once

#include <cstddef>
#include <stdexcept>
#include <vector>

namespace EMTG
{
    namespace Integration
    {
        enum class STMErrorControlPolicy
        {
            state_only = 0,
            state_and_stm = 1
        };

        // Local embedded-error contract used by adaptive integration. Absolute
        // tolerances carry the units of their corresponding state or STM entry.
        struct AdaptiveErrorControlSettings
        {
            double relative_tolerance = 1.0e-8;
            std::vector<double> absolute_tolerances;
            double stm_relative_tolerance = 1.0e-8;
            std::vector<double> stm_absolute_tolerances;
            STMErrorControlPolicy stm_policy = STMErrorControlPolicy::state_and_stm;

            void validate(const size_t state_size, const size_t stm_size) const;
        };

        struct EmbeddedErrorEstimate
        {
            double state_normalized_error = 0.0;
            double stm_normalized_error = 0.0;
            double combined_normalized_error = 0.0;
            size_t worst_state_index = 0;
            size_t worst_stm_row = 0;
            size_t worst_stm_column = 0;
        };
    }
}
