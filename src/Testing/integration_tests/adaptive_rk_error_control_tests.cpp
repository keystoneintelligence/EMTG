// EMTG: Evolutionary Mission Trajectory Generator
// An open-source global optimization tool for preliminary mission design
// Provided by NASA Goddard Space Flight Center
//
// Copyright (c) 2013 - 2024 United States Government as represented by the
// Administrator of the National Aeronautics and Space Administration.
// All Other Rights Reserved.

#include "ExplicitRungeKutta.h"
#include "IntegratedAdaptiveStepPropagator.h"
#include "IntegratedFixedStepPropagator.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <iomanip>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

namespace
{
    const size_t driver_state_size = 8;
    const size_t driver_stm_size = 8;
    const size_t driver_epoch_index = 7;
    const double driver_omega = 0.6;
    const double driver_forcing_omega = 7.0;

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

    class DriverAuditIntegrand : public EMTG::Integration::Integrand
    {
    public:
        explicit DriverAuditIntegrand(const size_t stm_size = driver_stm_size) : evaluation_count(0)
        {
            this->state_propagation_matrix.resize(stm_size, stm_size, 0.0);
        }

        void evaluate(const EMTG::math::Matrix<doubleType>& state,
                      EMTG::math::Matrix<doubleType>& state_dot,
                      const bool& generate_derivatives) override
        {
            ++this->evaluation_count;

            for (size_t k = 0; k < driver_state_size; ++k)
            {
                state_dot(k) = 0.0;
            }

            state_dot(0) = state(1);
            state_dot(1) = -driver_omega * driver_omega * state(0);
            state_dot(2) = std::cos(driver_forcing_omega * this->current_independent_variable);
            state_dot(driver_epoch_index) = 1.0;

            if (generate_derivatives)
            {
                this->state_propagation_matrix.assign_zeros();
                this->state_propagation_matrix(0, 1) = 1.0;
                this->state_propagation_matrix(1, 0) = -driver_omega * driver_omega;
            }
        }

        void evaluate(const EMTG::math::Matrix<doubleType>& state,
                      EMTG::math::Matrix<doubleType>& state_dot,
                      const EMTG::math::Matrix<doubleType>&,
                      const bool& generate_derivatives) override
        {
            this->evaluate(state, state_dot, generate_derivatives);
        }

        size_t getEvaluationCount() const
        {
            return this->evaluation_count;
        }

    private:
        size_t evaluation_count;
    };

    struct DriverPropagationResult
    {
        std::string label;
        double propagation_span;
        bool need_stm;
        double terminal_error;
        double epoch_error;
        double stm_error;
        double elapsed_ms;
        size_t accepted_steps;
        size_t evaluations;
        double history_sum;
        bool history_steps_have_expected_sign;
        std::vector<double> final_state;
        std::vector<double> propagation_history;
    };

    EMTG::math::Matrix<doubleType> make_driver_initial_state()
    {
        EMTG::math::Matrix<doubleType> state(driver_state_size, 1, 0.0);
        state(0) = 1.0;
        state(1) = 0.0;
        state(2) = 0.0;
        state(driver_epoch_index) = 0.0;
        return state;
    }

    std::vector<double> exact_driver_state(const double propagation_span)
    {
        std::vector<double> exact(driver_state_size, 0.0);
        exact[0] = std::cos(driver_omega * propagation_span);
        exact[1] = -driver_omega * std::sin(driver_omega * propagation_span);
        exact[2] = std::sin(driver_forcing_omega * propagation_span) / driver_forcing_omega;
        exact[driver_epoch_index] = propagation_span;
        return exact;
    }

    EMTG::math::Matrix<double> exact_driver_stm(const double propagation_span)
    {
        EMTG::math::Matrix<double> exact(driver_stm_size, EMTG::math::identity);
        exact(0, 0) = std::cos(driver_omega * propagation_span);
        exact(0, 1) = std::sin(driver_omega * propagation_span) / driver_omega;
        exact(1, 0) = -driver_omega * std::sin(driver_omega * propagation_span);
        exact(1, 1) = std::cos(driver_omega * propagation_span);
        return exact;
    }

    std::vector<double> extract_state(const EMTG::math::Matrix<doubleType>& state)
    {
        std::vector<double> values(driver_state_size, 0.0);

        for (size_t k = 0; k < driver_state_size; ++k)
        {
            values[k] = state(k) _GETVALUE;
        }

        return values;
    }

    double max_abs_state_error(const std::vector<double>& observed,
                               const std::vector<double>& expected)
    {
        double max_error = 0.0;

        for (size_t k = 0; k < observed.size(); ++k)
        {
            max_error = std::max(max_error, std::fabs(observed[k] - expected[k]));
        }

        return max_error;
    }

    double max_abs_stm_error(const EMTG::math::Matrix<double>& observed,
                             const EMTG::math::Matrix<double>& expected)
    {
        double max_error = 0.0;

        for (size_t row = 0; row < driver_stm_size; ++row)
        {
            for (size_t column = 0; column < driver_stm_size; ++column)
            {
                max_error = std::max(max_error, std::fabs(observed(row, column) - expected(row, column)));
            }
        }

        return max_error;
    }

    double sum_history(const std::vector<double>& propagation_history)
    {
        double sum = 0.0;

        for (size_t k = 0; k < propagation_history.size(); ++k)
        {
            sum += propagation_history[k];
        }

        return sum;
    }

    bool history_steps_have_expected_sign(const std::vector<double>& propagation_history,
                                          const double propagation_span)
    {
        if (propagation_history.empty())
        {
            return false;
        }

        for (size_t k = 0; k < propagation_history.size(); ++k)
        {
            if (propagation_span > 0.0 && propagation_history[k] <= 0.0)
            {
                return false;
            }
            else if (propagation_span < 0.0 && propagation_history[k] >= 0.0)
            {
                return false;
            }
        }

        return true;
    }


    DriverPropagationResult finalize_driver_result(const std::string& label,
                                                   const double propagation_span,
                                                   const bool need_stm,
                                                   const EMTG::math::Matrix<doubleType>& state_right,
                                                   const EMTG::math::Matrix<double>& STM,
                                                   const std::vector<double>& propagation_history,
                                                   const size_t evaluations,
                                                   const double elapsed_ms)
    {
        DriverPropagationResult result;
        result.label = label;
        result.propagation_span = propagation_span;
        result.need_stm = need_stm;
        result.final_state = extract_state(state_right);
        result.terminal_error = max_abs_state_error(result.final_state, exact_driver_state(propagation_span));
        result.epoch_error = std::fabs(result.final_state[driver_epoch_index] - propagation_span);
        result.stm_error = need_stm ? max_abs_stm_error(STM, exact_driver_stm(propagation_span)) : 0.0;
        result.elapsed_ms = elapsed_ms;
        result.accepted_steps = propagation_history.size();
        result.evaluations = evaluations;
        result.propagation_history = propagation_history;
        result.history_sum = sum_history(propagation_history);
        result.history_steps_have_expected_sign = history_steps_have_expected_sign(propagation_history, propagation_span);
        return result;
    }

    DriverPropagationResult run_integrated_driver(const std::string& label,
                                                  const bool use_adaptive,
                                                  const double propagation_span,
                                                  const double step_size,
                                                  const double tolerance,
                                                  const bool need_stm)
    {
        DriverAuditIntegrand integrand;
        EMTG::Integration::ExplicitRungeKutta rk(&integrand,
                                                 EMTG::IntegrationCoefficientsType::rkdp87,
                                                 driver_state_size,
                                                 driver_stm_size);

        EMTG::math::Matrix<doubleType> state_left = make_driver_initial_state();
        EMTG::math::Matrix<doubleType> state_right(driver_state_size, 1, 0.0);
        EMTG::math::Matrix<double> STM(driver_stm_size, EMTG::math::identity);
        double boundary_target_dstep_sized_prop_var = 0.0;

        const auto start = std::chrono::steady_clock::now();

        if (use_adaptive)
        {
            EMTG::Astrodynamics::IntegratedAdaptiveStepPropagator propagator(driver_state_size, driver_stm_size);
            EMTG::math::Matrix<double> error_scaling_factors(driver_state_size + driver_stm_size * driver_stm_size, 1, 1.0);
            propagator.setIntegrand(&integrand);
            propagator.setIntegrationScheme(&rk);
            propagator.setStateLeft(state_left);
            propagator.setStateRight(state_right);
            propagator.setSTM(STM);
            propagator.setCurrentEpoch(state_left(driver_epoch_index));
            propagator.setCurrentIndependentVariable(state_left(driver_epoch_index));
            propagator.setIndexOfEpochInStateVec(driver_epoch_index);
            propagator.setPropagationStepSize(step_size);
            propagator.setStorePropagationHistory(true);
            propagator.setdStepSizedPropVar(0.0);
            propagator.setBoundaryTarget_dStepSizedPropVar(&boundary_target_dstep_sized_prop_var);
            propagator.setErrorScalingFactors(error_scaling_factors);
            propagator.setTolerance(tolerance);
            propagator.propagate(propagation_span, need_stm);

            const auto stop = std::chrono::steady_clock::now();
            return finalize_driver_result(label,
                                          propagation_span,
                                          need_stm,
                                          state_right,
                                          STM,
                                          propagator.getPropagationHistory(),
                                          integrand.getEvaluationCount(),
                                          std::chrono::duration<double, std::milli>(stop - start).count());
        }

        EMTG::Astrodynamics::IntegratedFixedStepPropagator propagator(driver_state_size, driver_stm_size);
        propagator.setIntegrand(&integrand);
        propagator.setIntegrationScheme(&rk);
        propagator.setStateLeft(state_left);
        propagator.setStateRight(state_right);
        propagator.setSTM(STM);
        propagator.setCurrentEpoch(state_left(driver_epoch_index));
        propagator.setCurrentIndependentVariable(state_left(driver_epoch_index));
        propagator.setIndexOfEpochInStateVec(driver_epoch_index);
        propagator.setPropagationStepSize(step_size);
        propagator.setStorePropagationHistory(true);
        propagator.setBoundaryTarget_dStepSizedPropVar(&boundary_target_dstep_sized_prop_var);
        propagator.propagate(propagation_span, need_stm);

        const auto stop = std::chrono::steady_clock::now();
        return finalize_driver_result(label,
                                      propagation_span,
                                      need_stm,
                                      state_right,
                                      STM,
                                      propagator.getPropagationHistory(),
                                      integrand.getEvaluationCount(),
                                      std::chrono::duration<double, std::milli>(stop - start).count());
    }

    DriverPropagationResult run_legacy_adaptive_driver_simulation(const std::string& label,
                                                                  const double propagation_span,
                                                                  const double step_size,
                                                                  const double tolerance,
                                                                  const bool need_stm)
    {
        DriverAuditIntegrand integrand;
        EMTG::Integration::ExplicitRungeKutta rk(&integrand,
                                                 EMTG::IntegrationCoefficientsType::rkdp87,
                                                 driver_state_size,
                                                 driver_stm_size);

        doubleType current_epoch = 0.0;
        doubleType current_independent_variable = 0.0;
        rk.setLeftHandIndependentVariablePtr(current_independent_variable);

        EMTG::math::Matrix<doubleType> state_left = make_driver_initial_state();
        EMTG::math::Matrix<doubleType> state_right(driver_state_size, 1, 0.0);
        EMTG::math::Matrix<doubleType> control(4, 1, 0.0);
        EMTG::math::Matrix<double> STM_left(driver_stm_size, EMTG::math::identity);
        EMTG::math::Matrix<double> STM_right(driver_stm_size, EMTG::math::identity);
        EMTG::math::Matrix<double> error_scaling_factors(driver_state_size + driver_stm_size * driver_stm_size, 1, 1.0);
        std::vector<double> propagation_history;

        doubleType accumulatedH = 0.0;
        doubleType effectiveH = step_size;
        doubleType nextStep = effectiveH;
        doubleType adaptive_step_error = 1.0e-20;

        if (effectiveH > propagation_span)
        {
            effectiveH = propagation_span;
        }

        bool last_step = false;
        size_t safety_counter = 0;

        const auto start = std::chrono::steady_clock::now();

        do
        {
            do
            {
                effectiveH = nextStep;
                rk.errorControlledStep(state_left,
                                       STM_left,
                                       state_right,
                                       STM_right,
                                       control,
                                       effectiveH,
                                       0.0,
                                       need_stm,
                                       adaptive_step_error,
                                       error_scaling_factors);

                if (!last_step)
                {
                    if (adaptive_step_error == 0.0)
                    {
                        adaptive_step_error = 1.0e-15;
                    }

                    if (adaptive_step_error >= tolerance)
                    {
                        nextStep = 0.98 * effectiveH * std::pow(tolerance / (adaptive_step_error _GETVALUE), 0.17);
                    }
                    else
                    {
                        nextStep = 1.01 * effectiveH * std::pow(tolerance / (adaptive_step_error _GETVALUE), 0.18);

                        if (std::fabs(propagation_span - (accumulatedH _GETVALUE)) < std::fabs(nextStep _GETVALUE) && !last_step)
                        {
                            nextStep = propagation_span - accumulatedH;
                        }
                    }

                    if (std::fabs(propagation_span - (accumulatedH _GETVALUE)) < std::fabs(nextStep _GETVALUE) && !last_step)
                    {
                        nextStep = propagation_span - accumulatedH;
                    }

                    if (std::fabs(nextStep _GETVALUE) < 1.0e-13)
                    {
                        throw std::runtime_error("legacy adaptive simulation step size underflow");
                    }
                }
                else if (adaptive_step_error > tolerance && last_step)
                {
                    last_step = false;
                    nextStep = 0.98 * effectiveH * std::pow(tolerance / (adaptive_step_error _GETVALUE), 0.17);
                }

            } while (adaptive_step_error > tolerance);

            state_left = state_right;
            STM_left = STM_right;
            propagation_history.push_back(state_right(driver_epoch_index) _GETVALUE - state_left(driver_epoch_index) _GETVALUE);
            accumulatedH += effectiveH;
            current_epoch += effectiveH;

            if (std::fabs(propagation_span - (accumulatedH _GETVALUE)) < std::fabs(nextStep _GETVALUE)
                && std::fabs(propagation_span - (accumulatedH _GETVALUE)) > 0.0
                && !last_step)
            {
                nextStep = propagation_span - accumulatedH;
                last_step = true;
            }

            if (++safety_counter > 100000)
            {
                throw std::runtime_error("legacy adaptive simulation exceeded safety counter");
            }

        } while (std::fabs(accumulatedH _GETVALUE) < std::fabs(propagation_span));

        const auto stop = std::chrono::steady_clock::now();
        return finalize_driver_result(label,
                                      propagation_span,
                                      need_stm,
                                      state_right,
                                      STM_right,
                                      propagation_history,
                                      integrand.getEvaluationCount(),
                                      std::chrono::duration<double, std::milli>(stop - start).count());
    }

    void print_driver_result(const DriverPropagationResult& result)
    {
        std::cout << "driver_ab " << result.label
                  << " span=" << std::fixed << std::setprecision(3) << result.propagation_span
                  << " needSTM=" << result.need_stm
                  << " terminal_error=" << std::scientific << result.terminal_error
                  << " epoch_error=" << result.epoch_error
                  << " stm_error=" << result.stm_error
                  << " accepted_steps=" << std::dec << result.accepted_steps
                  << " evaluations=" << result.evaluations
                  << " history_sum=" << std::scientific << result.history_sum
                  << " history_sign_ok=" << result.history_steps_have_expected_sign
                  << " elapsed_ms=" << std::fixed << std::setprecision(3) << result.elapsed_ms
                  << std::endl;
    }

    void print_ephemeris_row(const DriverPropagationResult& result)
    {
        std::cout << "ephemeris " << result.label
                  << " t=" << std::fixed << std::setprecision(9) << result.final_state[driver_epoch_index]
                  << " x0=" << std::scientific << result.final_state[0]
                  << " x1=" << result.final_state[1]
                  << " forced_state=" << result.final_state[2]
                  << " epoch=" << result.final_state[driver_epoch_index]
                  << std::endl;
    }

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

    bool test_integrated_adaptive_backward_no_stm()
    {
        const DriverPropagationResult result = run_integrated_driver("adaptive_backward_noSTM",
                                                                     true,
                                                                     -10.0,
                                                                     2.0,
                                                                     1.0e-9,
                                                                     false);
        print_driver_result(result);
        print_ephemeris_row(result);

        if (result.terminal_error > 1.0e-7)
        {
            std::cerr << "Backward adaptive propagation terminal error is too large: "
                      << std::scientific << result.terminal_error << std::endl;
            return false;
        }

        if (result.epoch_error > 1.0e-10)
        {
            std::cerr << "Backward adaptive propagation ended at the wrong epoch: "
                      << std::scientific << result.epoch_error << std::endl;
            return false;
        }

        if (std::fabs(result.history_sum + 10.0) > 1.0e-9 || !result.history_steps_have_expected_sign)
        {
            std::cerr << "Backward adaptive propagation history is inconsistent: sum="
                      << std::scientific << result.history_sum
                      << " sign_ok=" << result.history_steps_have_expected_sign << std::endl;
            return false;
        }

        return true;
    }

    bool test_integrated_adaptive_short_span_no_overshoot()
    {
        const DriverPropagationResult result = run_integrated_driver("adaptive_short_span_noSTM",
                                                                     true,
                                                                     0.25,
                                                                     2.0,
                                                                     1.0e-6,
                                                                     false);
        print_driver_result(result);
        print_ephemeris_row(result);

        if (result.epoch_error > 1.0e-12)
        {
            std::cerr << "Short-span adaptive propagation overshot the target epoch: "
                      << std::scientific << result.epoch_error << std::endl;
            return false;
        }

        if (std::fabs(result.history_sum - 0.25) > 1.0e-12 || !result.history_steps_have_expected_sign)
        {
            std::cerr << "Short-span adaptive propagation history is inconsistent: sum="
                      << std::scientific << result.history_sum
                      << " sign_ok=" << result.history_steps_have_expected_sign << std::endl;
            return false;
        }

        return true;
    }

    bool test_integrated_adaptive_stm_vs_no_stm()
    {
        const DriverPropagationResult no_stm = run_integrated_driver("adaptive_forward_noSTM",
                                                                     true,
                                                                     10.0,
                                                                     2.0,
                                                                     1.0e-9,
                                                                     false);
        const DriverPropagationResult with_stm = run_integrated_driver("adaptive_forward_STM",
                                                                       true,
                                                                       10.0,
                                                                       2.0,
                                                                       1.0e-9,
                                                                       true);
        print_driver_result(no_stm);
        print_driver_result(with_stm);
        print_ephemeris_row(no_stm);
        print_ephemeris_row(with_stm);

        const double state_difference = max_abs_state_error(no_stm.final_state, with_stm.final_state);
        if (state_difference > 1.0e-7)
        {
            std::cerr << "Adaptive STM and no-STM final states diverged: "
                      << std::scientific << state_difference << std::endl;
            return false;
        }

        if (with_stm.stm_error > 1.0e-7)
        {
            std::cerr << "Adaptive STM error is too large: "
                      << std::scientific << with_stm.stm_error << std::endl;
            return false;
        }

        return true;
    }

    bool test_driver_ab_comparison()
    {
        const DriverPropagationResult legacy_forward = run_legacy_adaptive_driver_simulation("legacy_adaptive_forward_noSTM",
                                                                                            10.0,
                                                                                            2.0,
                                                                                            1.0e-9,
                                                                                            false);
        const DriverPropagationResult legacy_backward = run_legacy_adaptive_driver_simulation("legacy_adaptive_backward_noSTM",
                                                                                             -10.0,
                                                                                             2.0,
                                                                                             1.0e-9,
                                                                                             false);
        const DriverPropagationResult fixed_forward = run_integrated_driver("fixed_forward_noSTM",
                                                                            false,
                                                                            10.0,
                                                                            2.0,
                                                                            1.0e-9,
                                                                            false);
        const DriverPropagationResult fixed_forward_step_050 = run_integrated_driver("fixed_forward_noSTM_step0p50",
                                                                                    false,
                                                                                    10.0,
                                                                                    0.5,
                                                                                    1.0e-9,
                                                                                    false);
        const DriverPropagationResult fixed_forward_step_010 = run_integrated_driver("fixed_forward_noSTM_step0p10",
                                                                                    false,
                                                                                    10.0,
                                                                                    0.1,
                                                                                    1.0e-9,
                                                                                    false);
        const DriverPropagationResult adaptive_forward = run_integrated_driver("adaptive_forward_noSTM_ab",
                                                                               true,
                                                                               10.0,
                                                                               2.0,
                                                                               1.0e-9,
                                                                               false);

        print_driver_result(legacy_forward);
        print_driver_result(legacy_backward);
        print_driver_result(fixed_forward);
        print_driver_result(fixed_forward_step_050);
        print_driver_result(fixed_forward_step_010);
        print_driver_result(adaptive_forward);
        print_ephemeris_row(legacy_forward);
        print_ephemeris_row(legacy_backward);
        print_ephemeris_row(fixed_forward);
        print_ephemeris_row(fixed_forward_step_050);
        print_ephemeris_row(fixed_forward_step_010);
        print_ephemeris_row(adaptive_forward);

        if (!(adaptive_forward.terminal_error < fixed_forward.terminal_error))
        {
            std::cerr << "Expected adaptive driver to improve terminal accuracy over fixed-step A/B case: fixed="
                      << std::scientific << fixed_forward.terminal_error
                      << " adaptive=" << adaptive_forward.terminal_error << std::endl;
            return false;
        }

        if (legacy_backward.epoch_error < 1.0)
        {
            std::cerr << "Legacy backward simulation unexpectedly reached the correct epoch; audit fixture no longer exposes the old bug."
                      << std::endl;
            return false;
        }

        return true;
    }

    bool test_component_atol_rtol_contract()
    {
        OscillatoryIntegrand integrand(driver_forcing_omega);
        EMTG::Integration::ExplicitRungeKutta rk(&integrand,
                                                 EMTG::IntegrationCoefficientsType::rkdp87,
                                                 1,
                                                 1);
        doubleType independent_variable = 0.0;
        rk.setLeftHandIndependentVariablePtr(independent_variable);

        EMTG::Integration::AdaptiveErrorControlSettings settings;
        settings.relative_tolerance = 1.0e-6;
        settings.absolute_tolerances = {1.0e-12};
        settings.stm_policy = EMTG::Integration::STMErrorControlPolicy::state_only;
        rk.setAdaptiveErrorControlSettings(settings);

        EMTG::math::Matrix<doubleType> small_left(1, 1, 0.0);
        EMTG::math::Matrix<doubleType> large_left(1, 1, 1.0e8);
        EMTG::math::Matrix<doubleType> state_right(1, 1, 0.0);
        EMTG::math::Matrix<doubleType> control(1, 1, 0.0);
        EMTG::math::Matrix<double> stm_left(1, EMTG::math::identity);
        EMTG::math::Matrix<double> stm_right(1, EMTG::math::identity);
        EMTG::math::Matrix<double> legacy_scaling(2, 1, 1.0);
        doubleType small_error = 0.0;
        doubleType large_error = 0.0;

        rk.errorControlledStep(small_left, stm_left, state_right, stm_right, control,
                               1.0, 0.0, false, small_error, legacy_scaling);
        independent_variable = 0.0;
        rk.errorControlledStep(large_left, stm_left, state_right, stm_right, control,
                               1.0, 0.0, false, large_error, legacy_scaling);

        if (!(small_error > large_error * 1.0e6))
        {
            std::cerr << "Component error was not normalized by atol + rtol*max(|left|,|trial|): small="
                      << small_error << " large=" << large_error << std::endl;
            return false;
        }

        bool rejected_invalid_tolerance = false;
        settings.absolute_tolerances[0] = 0.0;
        try
        {
            rk.setAdaptiveErrorControlSettings(settings);
        }
        catch (const std::invalid_argument&)
        {
            rejected_invalid_tolerance = true;
        }
        return rejected_invalid_tolerance;
    }

    bool test_separate_stm_error_norm()
    {
        DriverAuditIntegrand integrand;
        EMTG::Integration::ExplicitRungeKutta rk(&integrand,
                                                 EMTG::IntegrationCoefficientsType::rkdp87,
                                                 driver_state_size,
                                                 driver_stm_size);
        doubleType independent_variable = 0.0;
        rk.setLeftHandIndependentVariablePtr(independent_variable);
        EMTG::math::Matrix<doubleType> state_left = make_driver_initial_state();
        EMTG::math::Matrix<doubleType> state_right(driver_state_size, 1, 0.0);
        EMTG::math::Matrix<doubleType> control(4, 1, 0.0);
        EMTG::math::Matrix<double> stm_left(driver_stm_size, EMTG::math::identity);
        EMTG::math::Matrix<double> stm_right(driver_stm_size, EMTG::math::identity);
        EMTG::math::Matrix<double> legacy_scaling(driver_state_size + driver_stm_size * driver_stm_size, 1, 1.0);
        EMTG::Integration::AdaptiveErrorControlSettings settings;
        settings.relative_tolerance = 1.0;
        settings.absolute_tolerances.assign(driver_state_size, 1.0e6);
        settings.stm_relative_tolerance = 1.0e-12;
        settings.stm_absolute_tolerances.assign(driver_stm_size * driver_stm_size, 1.0e-14);
        settings.stm_policy = EMTG::Integration::STMErrorControlPolicy::state_and_stm;
        rk.setAdaptiveErrorControlSettings(settings);

        doubleType error = 0.0;
        rk.errorControlledStep(state_left, stm_left, state_right, stm_right, control,
                               2.0, 0.0, true, error, legacy_scaling);
        const auto estimate_with_stm = rk.getLastEmbeddedErrorEstimate();
        if (!(estimate_with_stm.stm_normalized_error > estimate_with_stm.state_normalized_error
              && estimate_with_stm.combined_normalized_error == estimate_with_stm.stm_normalized_error))
        {
            std::cerr << "Separate STM norm did not control the combined error." << std::endl;
            return false;
        }

        settings.stm_policy = EMTG::Integration::STMErrorControlPolicy::state_only;
        rk.setAdaptiveErrorControlSettings(settings);
        independent_variable = 0.0;
        rk.errorControlledStep(state_left, stm_left, state_right, stm_right, control,
                               2.0, 0.0, true, error, legacy_scaling);
        const auto estimate_state_only = rk.getLastEmbeddedErrorEstimate();
        return estimate_state_only.stm_normalized_error == 0.0
            && estimate_state_only.combined_normalized_error == estimate_state_only.state_normalized_error;
    }

    bool test_statistics_and_propagation_variable_derivative()
    {
        const size_t augmented_stm_size = driver_stm_size + 1;
        DriverAuditIntegrand integrand(augmented_stm_size);
        EMTG::Integration::ExplicitRungeKutta rk(&integrand,
                                                 EMTG::IntegrationCoefficientsType::rkdp87,
                                                 driver_state_size,
                                                 augmented_stm_size);
        EMTG::math::Matrix<doubleType> state_left = make_driver_initial_state();
        EMTG::math::Matrix<doubleType> state_right(driver_state_size, 1, 0.0);
        EMTG::math::Matrix<double> stm(augmented_stm_size, EMTG::math::identity);
        EMTG::math::Matrix<double> derivative_state(driver_state_size, 2, 0.0);
        EMTG::math::Matrix<double> legacy_scaling(driver_state_size + augmented_stm_size * augmented_stm_size, 1, 1.0);
        double boundary_derivative = 1.0;

        EMTG::Integration::AdaptiveErrorControlSettings settings;
        settings.relative_tolerance = 1.0e-10;
        settings.absolute_tolerances.assign(driver_state_size, 1.0e-12);
        settings.stm_policy = EMTG::Integration::STMErrorControlPolicy::state_only;

        EMTG::Astrodynamics::IntegratedAdaptiveStepPropagator propagator(driver_state_size, augmented_stm_size);
        propagator.setIntegrand(&integrand);
        propagator.setIntegrationScheme(&rk);
        propagator.setStateLeft(state_left);
        propagator.setStateRight(state_right);
        propagator.setSTM(stm);
        propagator.setdStatedIndependentVariable(derivative_state);
        propagator.setCurrentEpoch(0.0);
        propagator.setCurrentIndependentVariable(0.0);
        propagator.setIndexOfEpochInStateVec(driver_epoch_index);
        propagator.setPropagationStepSize(2.0);
        propagator.setInitialStepSize(2.0);
        propagator.setStorePropagationHistory(true);
        propagator.setBoundaryTarget_dStepSizedPropVar(&boundary_derivative);
        propagator.setErrorScalingFactors(legacy_scaling);
        propagator.setAdaptiveErrorControlSettings(settings);
        propagator.propagate(10.0, true);

        const EMTG::Astrodynamics::IntegrationStatistics& statistics = propagator.getIntegrationStatistics();
        if (statistics.accepted_steps != propagator.getPropagationHistory().size()
            || statistics.attempted_steps != statistics.accepted_steps + statistics.rejected_steps
            || statistics.rhs_evaluations != statistics.attempted_steps * 13
            || statistics.stm_rhs_evaluations != statistics.rhs_evaluations
            || statistics.rejected_steps == 0
            || statistics.minimum_accepted_step <= 0.0
            || statistics.maximum_accepted_step > 2.0
            || statistics.maximum_normalized_error <= 1.0)
        {
            std::cerr << "Adaptive integration statistics are inconsistent." << std::endl;
            return false;
        }

        const double expected_duration_derivative = -driver_omega * std::sin(driver_omega * 10.0);
        if (std::fabs(stm(0, augmented_stm_size - 1) - expected_duration_derivative) > 1.0e-6)
        {
            std::cerr << "Adaptive propagation-variable derivative is inconsistent: observed="
                      << stm(0, augmented_stm_size - 1)
                      << " expected=" << expected_duration_derivative << std::endl;
            return false;
        }

        EMTG::math::Matrix<doubleType> fixed_left = make_driver_initial_state();
        EMTG::math::Matrix<doubleType> fixed_right(driver_state_size, 1, 0.0);
        EMTG::math::Matrix<double> fixed_stm(augmented_stm_size, EMTG::math::identity);
        EMTG::Astrodynamics::IntegratedFixedStepPropagator fixed(driver_state_size, augmented_stm_size);
        fixed.setIntegrand(&integrand);
        fixed.setIntegrationScheme(&rk);
        fixed.setStateLeft(fixed_left);
        fixed.setStateRight(fixed_right);
        fixed.setSTM(fixed_stm);
        fixed.setCurrentEpoch(0.0);
        fixed.setCurrentIndependentVariable(0.0);
        fixed.setIndexOfEpochInStateVec(driver_epoch_index);
        fixed.setPropagationStepSize(3.0);
        fixed.setBoundaryTarget_dStepSizedPropVar(&boundary_derivative);
        fixed.propagate(10.0, false);
        const auto& fixed_statistics = fixed.getIntegrationStatistics();
        if (fixed_statistics.attempted_steps != 4
            || fixed_statistics.accepted_steps != 4
            || fixed_statistics.rejected_steps != 0
            || fixed_statistics.rhs_evaluations != 52
            || fixed_statistics.endpoint_capped_steps != 1
            || fixed_statistics.minimum_accepted_step != 1.0
            || fixed_statistics.maximum_accepted_step != 3.0)
        {
            std::cerr << "Fixed-step integration statistics are inconsistent." << std::endl;
            return false;
        }

        return true;
    }

    double run_dense_output_case(const double maximum_step)
    {
        DriverAuditIntegrand integrand;
        EMTG::Integration::ExplicitRungeKutta rk(&integrand,
                                                 EMTG::IntegrationCoefficientsType::rkdp87,
                                                 driver_state_size,
                                                 driver_stm_size);
        EMTG::math::Matrix<doubleType> state_left = make_driver_initial_state();
        EMTG::math::Matrix<doubleType> state_right(driver_state_size, 1, 0.0);
        EMTG::math::Matrix<double> stm(driver_stm_size, EMTG::math::identity);
        EMTG::math::Matrix<double> derivative_state(driver_state_size, 2, 0.0);
        EMTG::math::Matrix<double> legacy_scaling(driver_state_size + driver_stm_size * driver_stm_size, 1, 1.0);
        double boundary_derivative = 0.0;
        EMTG::Integration::AdaptiveErrorControlSettings settings;
        settings.relative_tolerance = 1.0;
        settings.absolute_tolerances.assign(driver_state_size, 100.0);
        settings.stm_policy = EMTG::Integration::STMErrorControlPolicy::state_only;

        EMTG::Astrodynamics::IntegratedAdaptiveStepPropagator propagator(driver_state_size, driver_stm_size);
        propagator.setIntegrand(&integrand);
        propagator.setIntegrationScheme(&rk);
        propagator.setStateLeft(state_left);
        propagator.setStateRight(state_right);
        propagator.setSTM(stm);
        propagator.setdStatedIndependentVariable(derivative_state);
        propagator.setCurrentEpoch(0.0);
        propagator.setCurrentIndependentVariable(0.0);
        propagator.setIndexOfEpochInStateVec(driver_epoch_index);
        propagator.setPropagationStepSize(maximum_step);
        propagator.setInitialStepSize(maximum_step);
        propagator.setBoundaryTarget_dStepSizedPropVar(&boundary_derivative);
        propagator.setErrorScalingFactors(legacy_scaling);
        propagator.setAdaptiveErrorControlSettings(settings);
        propagator.setRequestedEpochs({0.25, 0.75, 1.25, 1.75, 2.0});
        propagator.propagate(2.0, false);

        double maximum_error = 0.0;
        const auto& points = propagator.getRequestedEpochStates();
        if (points.size() != 5
            || propagator.getIntegrationStatistics().requested_epoch_evaluations != points.size())
            return std::numeric_limits<double>::infinity();
        for (const auto& point : points)
        {
            maximum_error = std::max(maximum_error,
                max_abs_state_error(extract_state(point.state), exact_driver_state(point.independent_variable)));
        }
        return maximum_error;
    }

    bool test_dense_output_and_events()
    {
        const double coarse_dense_error = run_dense_output_case(1.0);
        const double fine_dense_error = run_dense_output_case(0.5);
        if (!(fine_dense_error < 0.3 * coarse_dense_error && coarse_dense_error < 2.5e-1))
        {
            std::cerr << "Cubic Hermite dense output did not show the expected convergence trend: coarse="
                      << coarse_dense_error << " fine=" << fine_dense_error << std::endl;
            return false;
        }

        DriverAuditIntegrand integrand;
        EMTG::Integration::ExplicitRungeKutta rk(&integrand,
                                                 EMTG::IntegrationCoefficientsType::rkdp87,
                                                 driver_state_size,
                                                 driver_stm_size);
        EMTG::math::Matrix<doubleType> state_left = make_driver_initial_state();
        EMTG::math::Matrix<doubleType> state_right(driver_state_size, 1, 0.0);
        EMTG::math::Matrix<double> stm(driver_stm_size, EMTG::math::identity);
        EMTG::math::Matrix<double> derivative_state(driver_state_size, 2, 0.0);
        EMTG::math::Matrix<double> legacy_scaling(driver_state_size + driver_stm_size * driver_stm_size, 1, 1.0);
        double boundary_derivative = 0.0;
        EMTG::Integration::AdaptiveErrorControlSettings settings;
        settings.relative_tolerance = 1.0e-10;
        settings.absolute_tolerances.assign(driver_state_size, 1.0e-12);
        settings.stm_policy = EMTG::Integration::STMErrorControlPolicy::state_only;

        EMTG::Astrodynamics::IntegratedAdaptiveStepPropagator propagator(driver_state_size, driver_stm_size);
        propagator.setIntegrand(&integrand);
        propagator.setIntegrationScheme(&rk);
        propagator.setStateLeft(state_left);
        propagator.setStateRight(state_right);
        propagator.setSTM(stm);
        propagator.setdStatedIndependentVariable(derivative_state);
        propagator.setCurrentEpoch(0.0);
        propagator.setCurrentIndependentVariable(0.0);
        propagator.setIndexOfEpochInStateVec(driver_epoch_index);
        propagator.setPropagationStepSize(2.0);
        propagator.setBoundaryTarget_dStepSizedPropVar(&boundary_derivative);
        propagator.setErrorScalingFactors(legacy_scaling);
        propagator.setAdaptiveErrorControlSettings(settings);
        propagator.setScalarEvent(
            [](const EMTG::math::Matrix<doubleType>& state, const double) { return state(0) _GETVALUE; },
            -1,
            false,
            1.0e-9);
        propagator.propagate(5.0, false);

        const auto& events = propagator.getLocatedEvents();
        const double expected_event_time = std::acos(0.0) / driver_omega;
        if (events.size() != 1
            || std::fabs(events[0].independent_variable - expected_event_time) > 2.0e-7
            || std::fabs(events[0].event_value) > 2.0e-7
            || propagator.getIntegrationStatistics().event_landings != 1)
        {
            std::cerr << "Directional scalar event localization failed." << std::endl;
            return false;
        }

        state_left = make_driver_initial_state();
        state_right.assign_zeros();
        stm.construct_identity_matrix();
        propagator.setStateLeft(state_left);
        propagator.setStateRight(state_right);
        propagator.setSTM(stm);
        propagator.setCurrentEpoch(0.0);
        propagator.setCurrentIndependentVariable(0.0);
        propagator.setScalarEvent(
            [](const EMTG::math::Matrix<doubleType>& state, const double) { return state(0) _GETVALUE; },
            -1,
            true,
            1.0e-9);
        propagator.propagate(5.0, false);
        if (std::fabs(state_right(driver_epoch_index) _GETVALUE - expected_event_time) > 2.0e-7
            || propagator.getLocatedEvents().size() != 1
            || !propagator.getLocatedEvents()[0].terminal)
        {
            std::cerr << "Terminal scalar event did not stop at the located root." << std::endl;
            return false;
        }

        bool rejected_stm_dense_output = false;
        state_left = make_driver_initial_state();
        propagator.setStateLeft(state_left);
        propagator.setRequestedEpochs({0.1});
        try
        {
            propagator.propagate(1.0, true);
        }
        catch (const std::runtime_error&)
        {
            rejected_stm_dense_output = true;
        }
        return rejected_stm_dense_output;
    }

    struct ContinuitySweepResult
    {
        double span;
        double terminal_state;
        double propagated_derivative;
        size_t accepted_steps;
        size_t rejected_steps;
        std::vector<double> schedule;
    };

    ContinuitySweepResult run_continuity_sweep_point(const double span)
    {
        const size_t augmented_stm_size = driver_stm_size + 1;
        DriverAuditIntegrand integrand(augmented_stm_size);
        EMTG::Integration::ExplicitRungeKutta rk(&integrand,
                                                 EMTG::IntegrationCoefficientsType::rkdp87,
                                                 driver_state_size,
                                                 augmented_stm_size);
        EMTG::math::Matrix<doubleType> state_left = make_driver_initial_state();
        EMTG::math::Matrix<doubleType> state_right(driver_state_size, 1, 0.0);
        EMTG::math::Matrix<double> stm(augmented_stm_size, EMTG::math::identity);
        EMTG::math::Matrix<double> derivative_state(driver_state_size, 2, 0.0);
        EMTG::math::Matrix<double> legacy_scaling(driver_state_size + augmented_stm_size * augmented_stm_size, 1, 1.0);
        double boundary_derivative = 1.0;
        EMTG::Integration::AdaptiveErrorControlSettings settings;
        settings.relative_tolerance = 1.0e-11;
        settings.absolute_tolerances.assign(driver_state_size, 1.0e-13);
        settings.stm_policy = EMTG::Integration::STMErrorControlPolicy::state_only;

        EMTG::Astrodynamics::IntegratedAdaptiveStepPropagator propagator(driver_state_size, augmented_stm_size);
        propagator.setIntegrand(&integrand);
        propagator.setIntegrationScheme(&rk);
        propagator.setStateLeft(state_left);
        propagator.setStateRight(state_right);
        propagator.setSTM(stm);
        propagator.setdStatedIndependentVariable(derivative_state);
        propagator.setCurrentEpoch(0.0);
        propagator.setCurrentIndependentVariable(0.0);
        propagator.setIndexOfEpochInStateVec(driver_epoch_index);
        propagator.setPropagationStepSize(2.0);
        propagator.setInitialStepSize(2.0);
        propagator.setStorePropagationHistory(true);
        propagator.setBoundaryTarget_dStepSizedPropVar(&boundary_derivative);
        propagator.setErrorScalingFactors(legacy_scaling);
        propagator.setAdaptiveErrorControlSettings(settings);
        propagator.propagate(span, true);
        const auto& statistics = propagator.getIntegrationStatistics();
        return {span,
                state_right(0) _GETVALUE,
                stm(0, augmented_stm_size - 1),
                statistics.accepted_steps,
                statistics.rejected_steps,
                propagator.getPropagationHistory()};
    }

    bool test_derivative_continuity_sweep()
    {
        const ContinuitySweepResult center = run_continuity_sweep_point(10.0);
        const ContinuitySweepResult repeated = run_continuity_sweep_point(10.0);
        if (center.terminal_state != repeated.terminal_state
            || center.propagated_derivative != repeated.propagated_derivative
            || center.schedule != repeated.schedule)
        {
            std::cerr << "Repeated adaptive evaluations are not deterministic." << std::endl;
            return false;
        }

        for (const double perturbation : {1.0e-3, 1.0e-4, 1.0e-5})
        {
            const ContinuitySweepResult minus = run_continuity_sweep_point(10.0 - perturbation);
            const ContinuitySweepResult plus = run_continuity_sweep_point(10.0 + perturbation);
            const double finite_difference = (plus.terminal_state - minus.terminal_state) / (2.0 * perturbation);
            const double derivative_error = std::fabs(finite_difference - center.propagated_derivative);
            std::cout << "derivative_sweep perturbation=" << std::scientific << perturbation
                      << " finite_difference=" << finite_difference
                      << " propagated_derivative=" << center.propagated_derivative
                      << " derivative_error=" << derivative_error
                      << " steps_minus=" << minus.accepted_steps
                      << " steps_center=" << center.accepted_steps
                      << " steps_plus=" << plus.accepted_steps
                      << " rejects_minus=" << minus.rejected_steps
                      << " rejects_center=" << center.rejected_steps
                      << " rejects_plus=" << plus.rejected_steps
                      << std::endl;
            if (derivative_error > 2.0e-6)
            {
                std::cerr << "Adaptive propagated derivative does not agree with the local directional finite difference."
                          << std::endl;
                return false;
            }
        }
        return true;
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

    if (!test_integrated_adaptive_backward_no_stm())
    {
        return 1;
    }

    if (!test_integrated_adaptive_short_span_no_overshoot())
    {
        return 1;
    }

    if (!test_integrated_adaptive_stm_vs_no_stm())
    {
        return 1;
    }

    if (!test_driver_ab_comparison())
    {
        return 1;
    }

    if (!test_component_atol_rtol_contract()
        || !test_separate_stm_error_norm()
        || !test_statistics_and_propagation_variable_derivative()
        || !test_dense_output_and_events()
        || !test_derivative_continuity_sweep())
    {
        return 1;
    }

    return 0;
}
