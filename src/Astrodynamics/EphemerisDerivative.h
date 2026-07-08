// EMTG: Evolutionary Mission Trajectory Generator
// An open-source global optimization tool for preliminary mission design
// Provided by NASA Goddard Space Flight Center
//
// Copyright (c) 2013 - 2024 United States Government as represented by the
// Administrator of the National Aeronautics and Space Administration.
// All Other Rights Reserved.

#pragma once

#include <algorithm>

namespace EMTG
{
    namespace Astrodynamics
    {
        namespace EphemerisDerivative
        {
            constexpr double SPICE_DERIVATIVE_STEP_SIZE_SECONDS = 10.0;

            enum class Stencil
            {
                Central,
                Forward,
                Backward
            };

            inline Stencil select_stencil(const double epoch,
                                          const double window_open,
                                          const double window_close,
                                          const double preferred_step,
                                          double& step)
            {
                if (window_open >= window_close)
                {
                    step = preferred_step;
                    return Stencil::Central;
                }

                const double backward_span = epoch - window_open;
                const double forward_span = window_close - epoch;

                if (backward_span >= preferred_step && forward_span >= preferred_step)
                {
                    step = preferred_step;
                    return Stencil::Central;
                }

                if (forward_span >= preferred_step)
                {
                    step = preferred_step;
                    return Stencil::Forward;
                }

                if (backward_span >= preferred_step)
                {
                    step = preferred_step;
                    return Stencil::Backward;
                }

                if (backward_span > 0.0 && forward_span > 0.0)
                {
                    step = std::min(backward_span, forward_span);
                    return Stencil::Central;
                }

                if (forward_span > 0.0)
                {
                    step = forward_span;
                    return Stencil::Forward;
                }

                if (backward_span > 0.0)
                {
                    step = backward_span;
                    return Stencil::Backward;
                }

                step = preferred_step;
                return Stencil::Central;
            }

            inline void compute_state_derivative(const double current_state[6],
                                                 const double state_before[6],
                                                 const double state_after[6],
                                                 const double step,
                                                 const Stencil stencil,
                                                 double state_derivative[6])
            {
                state_derivative[0] = current_state[3];
                state_derivative[1] = current_state[4];
                state_derivative[2] = current_state[5];

                switch (stencil)
                {
                    case Stencil::Forward:
                        state_derivative[3] = (state_after[3] - current_state[3]) / step;
                        state_derivative[4] = (state_after[4] - current_state[4]) / step;
                        state_derivative[5] = (state_after[5] - current_state[5]) / step;
                        break;

                    case Stencil::Backward:
                        state_derivative[3] = (current_state[3] - state_before[3]) / step;
                        state_derivative[4] = (current_state[4] - state_before[4]) / step;
                        state_derivative[5] = (current_state[5] - state_before[5]) / step;
                        break;

                    case Stencil::Central:
                    default:
                        state_derivative[3] = (state_after[3] - state_before[3]) / (2.0 * step);
                        state_derivative[4] = (state_after[4] - state_before[4]) / (2.0 * step);
                        state_derivative[5] = (state_after[5] - state_before[5]) / (2.0 * step);
                        break;
                }
            }
        }
    }
}
