// EMTG: Evolutionary Mission Trajectory Generator
// Provided by NASA Goddard Space Flight Center

#pragma once

#include <algorithm>
#include <cstddef>
#include <stdexcept>
#include <string>
#include <vector>

namespace EMTG
{
    namespace math
    {
        // A dependency-free natural cubic spline. The endpoint second
        // derivatives are zero, matching the behavior of gsl_interp_cspline.
        class NaturalCubicSpline
        {
        public:
            NaturalCubicSpline() = default;

            NaturalCubicSpline(const std::vector<double>& x,
                               const std::vector<double>& y)
            {
                this->initialize(x, y);
            }

            void initialize(const std::vector<double>& x,
                            const std::vector<double>& y)
            {
                if (x.size() != y.size())
                    throw std::invalid_argument("NaturalCubicSpline x/y sizes differ");
                if (x.size() < 2)
                    throw std::invalid_argument("NaturalCubicSpline requires at least two samples");

                for (std::size_t i = 1; i < x.size(); ++i)
                {
                    if (!(x[i] > x[i - 1]))
                        throw std::invalid_argument("NaturalCubicSpline samples must be strictly increasing");
                }

                this->knots = x;
                const std::size_t intervalCount = x.size() - 1;
                this->a.assign(y.begin(), y.end() - 1);
                this->b.assign(intervalCount, 0.0);
                this->c.assign(intervalCount, 0.0);
                this->d.assign(intervalCount, 0.0);

                std::vector<double> secondDerivative(x.size(), 0.0);
                if (x.size() > 2)
                {
                    const std::size_t interiorCount = x.size() - 2;
                    std::vector<double> lower(interiorCount, 0.0);
                    std::vector<double> diagonal(interiorCount, 0.0);
                    std::vector<double> upper(interiorCount, 0.0);
                    std::vector<double> rhs(interiorCount, 0.0);

                    for (std::size_t row = 0; row < interiorCount; ++row)
                    {
                        const std::size_t i = row + 1;
                        const double hPrevious = x[i] - x[i - 1];
                        const double hNext = x[i + 1] - x[i];
                        lower[row] = hPrevious;
                        diagonal[row] = 2.0 * (hPrevious + hNext);
                        upper[row] = hNext;
                        rhs[row] = 6.0 * ((y[i + 1] - y[i]) / hNext
                                        - (y[i] - y[i - 1]) / hPrevious);
                    }

                    // Natural boundary values are zero, so the first lower
                    // and final upper coefficients do not contribute.
                    for (std::size_t row = 1; row < interiorCount; ++row)
                    {
                        const double factor = lower[row] / diagonal[row - 1];
                        diagonal[row] -= factor * upper[row - 1];
                        rhs[row] -= factor * rhs[row - 1];
                    }

                    secondDerivative[x.size() - 2] = rhs.back() / diagonal.back();
                    for (std::size_t row = interiorCount - 1; row-- > 0;)
                    {
                        secondDerivative[row + 1] =
                            (rhs[row] - upper[row] * secondDerivative[row + 2]) / diagonal[row];
                    }
                }

                for (std::size_t i = 0; i < intervalCount; ++i)
                {
                    const double h = x[i + 1] - x[i];
                    this->b[i] = (y[i + 1] - y[i]) / h
                               - h * (2.0 * secondDerivative[i] + secondDerivative[i + 1]) / 6.0;
                    this->c[i] = secondDerivative[i] / 2.0;
                    this->d[i] = (secondDerivative[i + 1] - secondDerivative[i]) / (6.0 * h);
                }
            }

            void clear() noexcept
            {
                this->knots.clear();
                this->a.clear();
                this->b.clear();
                this->c.clear();
                this->d.clear();
            }

            bool empty() const noexcept { return this->knots.empty(); }

            double evaluate(const double x) const
            {
                const std::size_t i = this->interval(x);
                const double offset = x - this->knots[i];
                return this->a[i] + offset * (this->b[i] + offset * (this->c[i] + offset * this->d[i]));
            }

            double derivative(const double x) const
            {
                const std::size_t i = this->interval(x);
                const double offset = x - this->knots[i];
                return this->b[i] + offset * (2.0 * this->c[i] + 3.0 * offset * this->d[i]);
            }

        private:
            std::size_t interval(const double x) const
            {
                if (this->knots.empty())
                    throw std::logic_error("NaturalCubicSpline is not initialized");
                if (x < this->knots.front() || x > this->knots.back())
                    throw std::out_of_range("NaturalCubicSpline evaluation is outside the sample range");
                if (x == this->knots.back())
                    return this->knots.size() - 2;

                return static_cast<std::size_t>(
                    std::upper_bound(this->knots.begin(), this->knots.end(), x)
                    - this->knots.begin() - 1);
            }

            std::vector<double> knots;
            std::vector<double> a;
            std::vector<double> b;
            std::vector<double> c;
            std::vector<double> d;
        };
    }
}
