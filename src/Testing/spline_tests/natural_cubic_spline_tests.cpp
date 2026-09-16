#include "NaturalCubicSpline.h"

#include <cmath>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

namespace
{
    void require_close(const double actual,
                       const double expected,
                       const char* label,
                       const double tolerance = 1.0e-12)
    {
        if (std::abs(actual - expected) > tolerance)
        {
            std::cerr << label << ": expected " << expected << ", got " << actual << '\n';
            throw std::runtime_error("spline value mismatch");
        }
    }

    template<typename ExceptionType, typename Callable>
    void require_throws(Callable&& operation, const char* label)
    {
        try
        {
            operation();
        }
        catch (const ExceptionType&)
        {
            return;
        }
        catch (const std::exception& error)
        {
            throw std::runtime_error(std::string(label)
                + " threw the wrong exception: " + error.what());
        }

        throw std::runtime_error(std::string(label) + " did not throw");
    }
}

int run_tests()
{
    // These values match gsl_interp_cspline for a small symmetric data set.
    EMTG::math::NaturalCubicSpline spline({0.0, 1.0, 2.0}, {0.0, 1.0, 0.0});

    require_close(spline.evaluate(0.0), 0.0, "left knot");
    require_close(spline.evaluate(0.5), 0.6875, "left interior");
    require_close(spline.evaluate(1.0), 1.0, "middle knot");
    require_close(spline.evaluate(1.5), 0.6875, "right interior");
    require_close(spline.evaluate(2.0), 0.0, "right knot");
    require_close(spline.derivative(0.0), 1.5, "left derivative");
    require_close(spline.derivative(0.5), 1.125, "left interior derivative");
    require_close(spline.derivative(1.0), 0.0, "middle-knot derivative");
    require_close(spline.derivative(1.5), -1.125, "right interior derivative");
    require_close(spline.derivative(2.0), -1.5, "right derivative");

    // An irregular grid exercises every tridiagonal coefficient. The expected
    // values and first derivatives were recorded from a natural cubic spline
    // implementation independent of EMTG.
    EMTG::math::NaturalCubicSpline irregular(
        {0.0, 0.5, 2.0, 3.5, 5.0},
        {1.0, -0.5, 2.0, 0.0, 3.0});
    const std::vector<double> query =
        {0.0, 0.25, 0.5, 1.25, 2.0, 2.75, 3.5, 4.25, 5.0};
    const std::vector<double> expectedValue =
        {1.0,
         0.1018518518518518,
         -0.5,
         0.3472222222222221,
         2.0,
         1.2291666666666665,
         0.0,
         0.7986111111111112,
         3.0};
    const std::vector<double> expectedDerivative =
        {-3.790123456790124,
         -3.197530864197531,
         -1.419753086419753,
         2.672839506172839,
         0.7283950617283951,
         -2.058641975308642,
         -0.4938271604938271,
         2.311728395061728,
         3.246913580246914};
    for (std::size_t i = 0; i < query.size(); ++i)
    {
        require_close(irregular.evaluate(query[i]), expectedValue[i], "irregular value");
        require_close(irregular.derivative(query[i]), expectedDerivative[i], "irregular derivative");
    }

    // Two samples must reduce exactly to linear interpolation.
    EMTG::math::NaturalCubicSpline linear({2.0, 5.0}, {7.0, 19.0});
    require_close(linear.evaluate(3.5), 13.0, "two-point value");
    require_close(linear.derivative(2.0), 4.0, "two-point left derivative");
    require_close(linear.derivative(5.0), 4.0, "two-point right derivative");

    EMTG::math::NaturalCubicSpline shifted(
        {1.0e9, 1.0e9 + 1.0, 1.0e9 + 2.0},
        {0.0, 1.0, 0.0});
    require_close(shifted.evaluate(1.0e9 + 0.5), 0.6875, "large epoch");

    require_throws<std::out_of_range>([&]() { spline.evaluate(-1.0); },
                                      "left out-of-range value");
    require_throws<std::out_of_range>([&]() { spline.evaluate(3.0); },
                                      "right out-of-range value");
    require_throws<std::out_of_range>([&]() { spline.derivative(-1.0); },
                                      "left out-of-range derivative");
    require_throws<std::out_of_range>([&]() { spline.derivative(3.0); },
                                      "right out-of-range derivative");

    EMTG::math::NaturalCubicSpline uninitialized;
    require_throws<std::logic_error>([&]() { uninitialized.evaluate(0.0); },
                                     "uninitialized value");
    require_throws<std::logic_error>([&]() { uninitialized.derivative(0.0); },
                                     "uninitialized derivative");
    require_throws<std::invalid_argument>(
        [&]() { uninitialized.initialize({0.0, 1.0}, {1.0}); },
        "mismatched sample counts");
    require_throws<std::invalid_argument>(
        [&]() { uninitialized.initialize({0.0}, {1.0}); },
        "too few samples");
    require_throws<std::invalid_argument>(
        [&]() { uninitialized.initialize({0.0, 1.0, 1.0}, {0.0, 1.0, 2.0}); },
        "duplicate knots");
    require_throws<std::invalid_argument>(
        [&]() { uninitialized.initialize({0.0, 2.0, 1.0}, {0.0, 1.0, 2.0}); },
        "decreasing knots");

    spline.clear();
    if (!spline.empty())
        throw std::runtime_error("clear did not empty the spline");
    require_throws<std::logic_error>([&]() { spline.evaluate(0.0); },
                                     "evaluation after clear");

    std::cout << "Natural cubic spline tests passed\n";
    return 0;
}

int main()
{
    try
    {
        return run_tests();
    }
    catch (const std::exception& error)
    {
        std::cerr << "Natural cubic spline test failure: " << error.what() << '\n';
        return 1;
    }
}
