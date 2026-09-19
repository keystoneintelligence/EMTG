#include "EphemerisDerivative.h"

#include <algorithm>
#include <cmath>
#include <iostream>
#include <stdexcept>
#include <string>

namespace
{
    struct AxisCoefficients
    {
        double position0;
        double velocity0;
        double acceleration0;
        double jerk;
        double snap;
    };

    const AxisCoefficients axes[3] =
    {
        { 1200.0,  7.2,  1.1e-3, -3.0e-5,  2.0e-7 },
        { -340.0, -2.4, -7.0e-4,  4.0e-5, -1.0e-7 },
        {  910.0,  0.8,  2.0e-4,  2.5e-5,  3.0e-7 }
    };

    void check_close(const std::string& label,
                     const double actual,
                     const double expected,
                     const double tolerance)
    {
        if (std::fabs(actual - expected) > tolerance)
        {
            throw std::runtime_error(label + " expected " + std::to_string(expected)
                                     + " but got " + std::to_string(actual));
        }
    }

    void check_true(const std::string& label, const bool condition)
    {
        if (!condition)
            throw std::runtime_error(label);
    }

    double position(const AxisCoefficients& axis, const double t)
    {
        return axis.position0
             + axis.velocity0 * t
             + 0.5 * axis.acceleration0 * t * t
             + axis.jerk * t * t * t / 6.0
             + axis.snap * t * t * t * t / 24.0;
    }

    double velocity(const AxisCoefficients& axis, const double t)
    {
        return axis.velocity0
             + axis.acceleration0 * t
             + 0.5 * axis.jerk * t * t
             + axis.snap * t * t * t / 6.0;
    }

    double acceleration(const AxisCoefficients& axis, const double t)
    {
        return axis.acceleration0
             + axis.jerk * t
             + 0.5 * axis.snap * t * t;
    }

    void state_at(const double t, double state[6])
    {
        for (size_t axis_index = 0; axis_index < 3; ++axis_index)
        {
            state[axis_index] = position(axes[axis_index], t);
            state[axis_index + 3] = velocity(axes[axis_index], t);
        }
    }

    void truth_derivative_at(const double t, double derivative[6])
    {
        for (size_t axis_index = 0; axis_index < 3; ++axis_index)
        {
            derivative[axis_index] = velocity(axes[axis_index], t);
            derivative[axis_index + 3] = acceleration(axes[axis_index], t);
        }
    }

    void legacy_forward_derivative(const double current[6],
                                   const double after[6],
                                   const double step,
                                   double derivative[6])
    {
        for (size_t state_index = 0; state_index < 6; ++state_index)
            derivative[state_index] = (after[state_index] - current[state_index]) / step;
    }

    double max_abs_error(const double actual[6], const double expected[6])
    {
        double error = 0.0;
        for (size_t state_index = 0; state_index < 6; ++state_index)
            error = std::max(error, std::fabs(actual[state_index] - expected[state_index]));

        return error;
    }
}

int main(int, char**)
{
    try
    {
        const double epoch = 12345.0;
        const double step = EMTG::Astrodynamics::EphemerisDerivative::SPICE_DERIVATIVE_STEP_SIZE_SECONDS;

        double current[6];
        double before[6];
        double after[6];
        double truth[6];
        double legacy[6];
        double improved[6];

        state_at(epoch, current);
        state_at(epoch - step, before);
        state_at(epoch + step, after);
        truth_derivative_at(epoch, truth);

        legacy_forward_derivative(current, after, step, legacy);
        EMTG::Astrodynamics::EphemerisDerivative::compute_state_derivative(
            current,
            before,
            after,
            step,
            EMTG::Astrodynamics::EphemerisDerivative::Stencil::Central,
            improved);

        for (size_t axis_index = 0; axis_index < 3; ++axis_index)
        {
            check_close("improved drdt component " + std::to_string(axis_index),
                        improved[axis_index],
                        truth[axis_index],
                        1.0e-12);
            check_true("legacy drdt keeps finite-difference bias " + std::to_string(axis_index),
                       std::fabs(legacy[axis_index] - truth[axis_index]) > 1.0e-3);
        }

        const double legacy_error = max_abs_error(legacy, truth);
        const double improved_error = max_abs_error(improved, truth);
        check_true("central/analytic derivative improves A/B max error",
                   improved_error < legacy_error * 0.02);

        double boundary_step = 0.0;
        EMTG::Astrodynamics::EphemerisDerivative::Stencil stencil =
            EMTG::Astrodynamics::EphemerisDerivative::select_stencil(105.0, 100.0, 1000.0, step, boundary_step);
        check_true("near-open coverage uses forward stencil",
                   stencil == EMTG::Astrodynamics::EphemerisDerivative::Stencil::Forward);
        check_close("near-open coverage keeps preferred step", boundary_step, step, 0.0);

        stencil = EMTG::Astrodynamics::EphemerisDerivative::select_stencil(995.0, 100.0, 1000.0, step, boundary_step);
        check_true("near-close coverage uses backward stencil",
                   stencil == EMTG::Astrodynamics::EphemerisDerivative::Stencil::Backward);
        check_close("near-close coverage keeps preferred step", boundary_step, step, 0.0);

        stencil = EMTG::Astrodynamics::EphemerisDerivative::select_stencil(500.0,
                                                                           100.0,
                                                                           1000.0,
                                                                           step,
                                                                           false,
                                                                           boundary_step);
        check_true("low-fidelity coverage uses forward stencil when available",
                   stencil == EMTG::Astrodynamics::EphemerisDerivative::Stencil::Forward);
        check_close("low-fidelity coverage keeps preferred step", boundary_step, step, 0.0);

        stencil = EMTG::Astrodynamics::EphemerisDerivative::select_stencil(995.0,
                                                                           100.0,
                                                                           1000.0,
                                                                           step,
                                                                           false,
                                                                           boundary_step);
        check_true("low-fidelity near-close coverage uses backward stencil",
                   stencil == EMTG::Astrodynamics::EphemerisDerivative::Stencil::Backward);
        check_close("low-fidelity near-close coverage keeps preferred step", boundary_step, step, 0.0);

        double one_sided[6];
        EMTG::Astrodynamics::EphemerisDerivative::compute_state_derivative(
            current,
            before,
            after,
            step,
            EMTG::Astrodynamics::EphemerisDerivative::Stencil::Forward,
            one_sided);
        for (size_t axis_index = 0; axis_index < 3; ++axis_index)
        {
            check_close("one-sided boundary drdt remains analytic " + std::to_string(axis_index),
                        one_sided[axis_index],
                        truth[axis_index],
                        1.0e-12);
        }

        double low_fidelity[6];
        EMTG::Astrodynamics::EphemerisDerivative::compute_state_derivative(
            current,
            before,
            after,
            step,
            EMTG::Astrodynamics::EphemerisDerivative::Stencil::Forward,
            false,
            low_fidelity);
        for (size_t state_index = 0; state_index < 6; ++state_index)
        {
            check_close("low-fidelity mode matches legacy one-sided component " + std::to_string(state_index),
                        low_fidelity[state_index],
                        legacy[state_index],
                        1.0e-12);
        }

        std::cout << "ephemeris derivative tests passed; legacy max error = "
                  << legacy_error << ", improved max error = " << improved_error << std::endl;
        return 0;
    }
    catch (const std::exception& error)
    {
        std::cerr << error.what() << std::endl;
        return 1;
    }
}
