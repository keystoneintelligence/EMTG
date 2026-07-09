// EMTG: Evolutionary Mission Trajectory Generator
// An open-source global optimization tool for preliminary mission design
// Provided by NASA Goddard Space Flight Center
//
// Copyright (c) 2013 - 2024 United States Government as represented by the
// Administrator of the National Aeronautics and Space Administration.
// All Other Rights Reserved.

#include "ExplicitRungeKutta.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <iomanip>
#include <iostream>
#include <stdexcept>
#include <string>

namespace
{
    class OscillatoryIntegrand : public EMTG::Integration::Integrand
    {
    public:
        explicit OscillatoryIntegrand(const double omega_in) : omega(omega_in)
        {
            this->state_propagation_matrix.resize(1, 1, 0.0);
        }

        void evaluate(const EMTG::math::Matrix<doubleType>&,
                      EMTG::math::Matrix<doubleType>& state_dot,
                      const bool&) override
        {
            state_dot(0) = std::cos(this->omega * this->current_independent_variable);
        }

        void evaluate(const EMTG::math::Matrix<doubleType>& state,
                      EMTG::math::Matrix<doubleType>& state_dot,
                      const EMTG::math::Matrix<doubleType>&,
                      const bool& generate_derivatives) override
        {
            this->evaluate(state, state_dot, generate_derivatives);
        }

    private:
        double omega;
    };

    struct PropagationResult
    {
        double tolerance;
        double terminal_error;
        double final_state;
        double elapsed_ms;
        size_t accepted_steps;
        size_t rejected_steps;
    };

    double compute_single_step_error()
    {
        OscillatoryIntegrand integrand(7.0);
        EMTG::Integration::ExplicitRungeKutta rk(&integrand, EMTG::IntegrationCoefficientsType::rkdp87, 1, 1);

        doubleType t_left = 0.0;
        rk.setLeftHandIndependentVariablePtr(t_left);

        EMTG::math::Matrix<doubleType> state_left(1, 1, 0.0);
        EMTG::math::Matrix<doubleType> state_right(1, 1, 0.0);
        EMTG::math::Matrix<doubleType> control(1, 1, 0.0);
        EMTG::math::Matrix<double> STM_left(1, EMTG::math::identity);
        EMTG::math::Matrix<double> STM_right(1, EMTG::math::identity);
        EMTG::math::Matrix<double> error_scaling_factors(1, 1, 1.0);

        doubleType error = 1.0e-20;
        rk.errorControlledStep(state_left,
                               STM_left,
                               state_right,
                               STM_right,
                               control,
                               1.0,
                               0.0,
                               false,
                               error,
                               error_scaling_factors);

        return error _GETVALUE;
    }

    PropagationResult propagate_oscillator(const double tolerance)
    {
        const double omega = 7.0;
        const double propagation_span = 10.0;
        const double max_step = 2.0;

        OscillatoryIntegrand integrand(omega);
        EMTG::Integration::ExplicitRungeKutta rk(&integrand, EMTG::IntegrationCoefficientsType::rkdp87, 1, 1);

        doubleType t_left = 0.0;
        rk.setLeftHandIndependentVariablePtr(t_left);

        EMTG::math::Matrix<doubleType> state_left(1, 1, 0.0);
        EMTG::math::Matrix<doubleType> state_right(1, 1, 0.0);
        EMTG::math::Matrix<doubleType> control(1, 1, 0.0);
        EMTG::math::Matrix<double> STM_left(1, EMTG::math::identity);
        EMTG::math::Matrix<double> STM_right(1, EMTG::math::identity);
        EMTG::math::Matrix<double> error_scaling_factors(1, 1, 1.0);

        double next_step = max_step;
        size_t accepted_steps = 0;
        size_t rejected_steps = 0;

        const auto start = std::chrono::steady_clock::now();

        while (t_left < propagation_span)
        {
            double effective_step = std::min(next_step, propagation_span - static_cast<double>(t_left));
            doubleType adaptive_step_error = 1.0e-20;

            do
            {
                adaptive_step_error = 1.0e-20;

                rk.errorControlledStep(state_left,
                                       STM_left,
                                       state_right,
                                       STM_right,
                                       control,
                                       effective_step,
                                       0.0,
                                       false,
                                       adaptive_step_error,
                                       error_scaling_factors);

                if (adaptive_step_error == 0.0)
                {
                    adaptive_step_error = 1.0e-15;
                }

                if (adaptive_step_error > tolerance)
                {
                    ++rejected_steps;
                    effective_step = 0.98 * effective_step * std::pow(tolerance / adaptive_step_error, 0.17);

                    if (std::fabs(effective_step) < 1.0e-13)
                    {
                        throw std::runtime_error("adaptive test step size underflow");
                    }
                }
            } while (adaptive_step_error > tolerance);

            state_left = state_right;
            t_left += effective_step;
            ++accepted_steps;

            next_step = 1.01 * effective_step * std::pow(tolerance / adaptive_step_error, 0.18);
            next_step = std::min(next_step, max_step);
        }

        const auto stop = std::chrono::steady_clock::now();
        const double elapsed_ms = std::chrono::duration<double, std::milli>(stop - start).count();
        const double exact = std::sin(omega * propagation_span) / omega;
        const double final_state = state_left(0) _GETVALUE;

        return { tolerance,
                 std::fabs(final_state - exact),
                 final_state,
                 elapsed_ms,
                 accepted_steps,
                 rejected_steps };
    }

    void print_result(const std::string& label, const PropagationResult& result)
    {
        std::cout << label
                  << " tolerance=" << std::scientific << result.tolerance
                  << " terminal_error=" << result.terminal_error
                  << " final_state=" << result.final_state
                  << " accepted_steps=" << result.accepted_steps
                  << " rejected_steps=" << result.rejected_steps
                  << " elapsed_ms=" << std::fixed << std::setprecision(3) << result.elapsed_ms
                  << std::endl;
    }
}

int main()
{
    const double single_step_error = compute_single_step_error();
    std::cout << "single_step_error=" << std::scientific << single_step_error << std::endl;

    if (single_step_error <= 1.0e-18)
    {
        std::cerr << "Expected nonzero embedded RK error estimate, got "
                  << std::scientific << single_step_error << std::endl;
        return 1;
    }

    const PropagationResult loose = propagate_oscillator(1.0e-5);
    const PropagationResult tight = propagate_oscillator(1.0e-9);

    print_result("loose", loose);
    print_result("tight", tight);

    if (!(tight.terminal_error < loose.terminal_error))
    {
        std::cerr << "Expected tighter tolerance to reduce terminal error: loose="
                  << std::scientific << loose.terminal_error
                  << " tight=" << tight.terminal_error << std::endl;
        return 1;
    }

    if (!(tight.accepted_steps > loose.accepted_steps))
    {
        std::cerr << "Expected tighter tolerance to require more accepted steps: loose="
                  << loose.accepted_steps
                  << " tight=" << tight.accepted_steps << std::endl;
        return 1;
    }

    return 0;
}
