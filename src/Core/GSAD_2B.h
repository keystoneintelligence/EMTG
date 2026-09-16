// EMTG local replacement for the missing GSAD_2B.h dependency.
// Provides a sparse forward-mode automatic differentiation scalar.

#pragma once

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <iosfwd>
#include <limits>
#include <ostream>
#include <utility>
#include <vector>

namespace GSAD
{
    class adouble
    {
    public:
        typedef std::pair<size_t, double> derivative_t;

        adouble() : value(0.0) {}
        adouble(const double value_in) : value(value_in) {}
        adouble(const int value_in) : value(static_cast<double>(value_in)) {}
        adouble(const size_t value_in) : value(static_cast<double>(value_in)) {}

        adouble& operator=(const double value_in)
        {
            this->value = value_in;
            this->derivatives.clear();
            return *this;
        }

        adouble& operator=(const int value_in)
        {
            return this->operator=(static_cast<double>(value_in));
        }

        adouble& operator=(const size_t value_in)
        {
            return this->operator=(static_cast<double>(value_in));
        }

        double getValue() const { return this->value; }
        void setValue(const double value_in) { this->value = value_in; }

        void setDerivative(const size_t derivative_index, const double derivative_value)
        {
            std::vector<derivative_t>::iterator entry = this->findDerivative(derivative_index);

            if (entry != this->derivatives.end() && entry->first == derivative_index)
            {
                if (derivative_value == 0.0)
                    this->derivatives.erase(entry);
                else
                    entry->second = derivative_value;
            }
            else if (derivative_value != 0.0)
            {
                this->derivatives.insert(entry, derivative_t(derivative_index, derivative_value));
            }
        }

        double getDerivative(const size_t derivative_index) const
        {
            std::vector<derivative_t>::const_iterator entry = this->findDerivative(derivative_index);
            return (entry != this->derivatives.end() && entry->first == derivative_index) ? entry->second : 0.0;
        }

        std::vector<size_t> getDerivativeIndicies() const
        {
            std::vector<size_t> derivative_indices;
            derivative_indices.reserve(this->derivatives.size());

            for (std::vector<derivative_t>::const_iterator entry = this->derivatives.begin();
                 entry != this->derivatives.end();
                 ++entry)
            {
                derivative_indices.push_back(entry->first);
            }

            return derivative_indices;
        }

        std::vector<size_t> getDerivativeIndices() const
        {
            return this->getDerivativeIndicies();
        }

        const std::vector<derivative_t>& getDerivativeVector() const
        {
            return this->derivatives;
        }

        void clearDerivatives()
        {
            this->derivatives.clear();
        }

        adouble operator+() const { return *this; }

        adouble operator-() const
        {
            adouble result(-this->value);
            result.derivatives.reserve(this->derivatives.size());

            for (std::vector<derivative_t>::const_iterator entry = this->derivatives.begin();
                 entry != this->derivatives.end();
                 ++entry)
            {
                result.derivatives.push_back(derivative_t(entry->first, -entry->second));
            }

            return result;
        }

        adouble& operator+=(const adouble& rhs)
        {
            this->value += rhs.value;
            combineDerivatives(rhs, 1.0);
            return *this;
        }

        adouble& operator-=(const adouble& rhs)
        {
            this->value -= rhs.value;
            combineDerivatives(rhs, -1.0);
            return *this;
        }

        adouble& operator*=(const adouble& rhs)
        {
            const double lhs_value = this->value;
            const std::vector<derivative_t> lhs_derivatives = this->derivatives;

            this->value *= rhs.value;
            this->derivatives.clear();

            for (std::vector<derivative_t>::const_iterator entry = lhs_derivatives.begin();
                 entry != lhs_derivatives.end();
                 ++entry)
            {
                this->setDerivative(entry->first, entry->second * rhs.value);
            }

            for (std::vector<derivative_t>::const_iterator entry = rhs.derivatives.begin();
                 entry != rhs.derivatives.end();
                 ++entry)
            {
                this->setDerivative(entry->first, this->getDerivative(entry->first) + lhs_value * entry->second);
            }

            return *this;
        }

        adouble& operator/=(const adouble& rhs)
        {
            const double lhs_value = this->value;
            const std::vector<derivative_t> lhs_derivatives = this->derivatives;
            const double inv_rhs = 1.0 / rhs.value;
            const double inv_rhs_squared = inv_rhs * inv_rhs;

            this->value *= inv_rhs;
            this->derivatives.clear();

            for (std::vector<derivative_t>::const_iterator entry = lhs_derivatives.begin();
                 entry != lhs_derivatives.end();
                 ++entry)
            {
                this->setDerivative(entry->first, entry->second * inv_rhs);
            }

            for (std::vector<derivative_t>::const_iterator entry = rhs.derivatives.begin();
                 entry != rhs.derivatives.end();
                 ++entry)
            {
                this->setDerivative(entry->first, this->getDerivative(entry->first) - lhs_value * entry->second * inv_rhs_squared);
            }

            return *this;
        }

        operator double() const { return this->value; }

        static adouble temp;
        static std::vector<size_t>::size_type point;

    private:
        double value;
        std::vector<derivative_t> derivatives;

        std::vector<derivative_t>::iterator findDerivative(const size_t derivative_index)
        {
            return std::lower_bound(this->derivatives.begin(), this->derivatives.end(), derivative_index, derivativeIndexLess());
        }

        std::vector<derivative_t>::const_iterator findDerivative(const size_t derivative_index) const
        {
            return std::lower_bound(this->derivatives.begin(), this->derivatives.end(), derivative_index, derivativeIndexLess());
        }

        struct derivativeIndexLess
        {
            bool operator()(const derivative_t& lhs, const size_t rhs) const
            {
                return lhs.first < rhs;
            }
        };

        void combineDerivatives(const adouble& rhs, const double rhs_scale)
        {
            for (std::vector<derivative_t>::const_iterator entry = rhs.derivatives.begin();
                 entry != rhs.derivatives.end();
                 ++entry)
            {
                this->setDerivative(entry->first, this->getDerivative(entry->first) + rhs_scale * entry->second);
            }
        }

        friend adouble makeUnaryResult(const adouble& x, const double value, const double derivative_scale);
    };

    inline adouble makeUnaryResult(const adouble& x, const double value, const double derivative_scale)
    {
        adouble result(value);
        const std::vector<adouble::derivative_t>& derivatives = x.getDerivativeVector();

        for (std::vector<adouble::derivative_t>::const_iterator entry = derivatives.begin();
             entry != derivatives.end();
             ++entry)
        {
            result.setDerivative(entry->first, derivative_scale * entry->second);
        }

        return result;
    }

    inline adouble operator+(adouble lhs, const adouble& rhs) { lhs += rhs; return lhs; }
    inline adouble operator-(adouble lhs, const adouble& rhs) { lhs -= rhs; return lhs; }
    inline adouble operator*(adouble lhs, const adouble& rhs) { lhs *= rhs; return lhs; }
    inline adouble operator/(adouble lhs, const adouble& rhs) { lhs /= rhs; return lhs; }

    inline adouble operator+(adouble lhs, const double rhs) { lhs += adouble(rhs); return lhs; }
    inline adouble operator+(const double lhs, const adouble& rhs) { return adouble(lhs) + rhs; }
    inline adouble operator-(adouble lhs, const double rhs) { lhs -= adouble(rhs); return lhs; }
    inline adouble operator-(const double lhs, const adouble& rhs) { return adouble(lhs) - rhs; }
    inline adouble operator*(adouble lhs, const double rhs) { lhs *= adouble(rhs); return lhs; }
    inline adouble operator*(const double lhs, const adouble& rhs) { return adouble(lhs) * rhs; }
    inline adouble operator/(adouble lhs, const double rhs) { lhs /= adouble(rhs); return lhs; }
    inline adouble operator/(const double lhs, const adouble& rhs) { return adouble(lhs) / rhs; }

    inline adouble operator+(adouble lhs, const int rhs) { return lhs + static_cast<double>(rhs); }
    inline adouble operator+(const int lhs, const adouble& rhs) { return static_cast<double>(lhs) + rhs; }
    inline adouble operator-(adouble lhs, const int rhs) { return lhs - static_cast<double>(rhs); }
    inline adouble operator-(const int lhs, const adouble& rhs) { return static_cast<double>(lhs) - rhs; }
    inline adouble operator*(adouble lhs, const int rhs) { return lhs * static_cast<double>(rhs); }
    inline adouble operator*(const int lhs, const adouble& rhs) { return static_cast<double>(lhs) * rhs; }
    inline adouble operator/(adouble lhs, const int rhs) { return lhs / static_cast<double>(rhs); }
    inline adouble operator/(const int lhs, const adouble& rhs) { return static_cast<double>(lhs) / rhs; }

    inline bool operator==(const adouble& lhs, const adouble& rhs) { return lhs.getValue() == rhs.getValue(); }
    inline bool operator!=(const adouble& lhs, const adouble& rhs) { return !(lhs == rhs); }
    inline bool operator<(const adouble& lhs, const adouble& rhs) { return lhs.getValue() < rhs.getValue(); }
    inline bool operator<=(const adouble& lhs, const adouble& rhs) { return lhs.getValue() <= rhs.getValue(); }
    inline bool operator>(const adouble& lhs, const adouble& rhs) { return lhs.getValue() > rhs.getValue(); }
    inline bool operator>=(const adouble& lhs, const adouble& rhs) { return lhs.getValue() >= rhs.getValue(); }

    inline bool operator==(const adouble& lhs, const double rhs) { return lhs.getValue() == rhs; }
    inline bool operator==(const double lhs, const adouble& rhs) { return lhs == rhs.getValue(); }
    inline bool operator!=(const adouble& lhs, const double rhs) { return !(lhs == rhs); }
    inline bool operator!=(const double lhs, const adouble& rhs) { return !(lhs == rhs); }
    inline bool operator<(const adouble& lhs, const double rhs) { return lhs.getValue() < rhs; }
    inline bool operator<(const double lhs, const adouble& rhs) { return lhs < rhs.getValue(); }
    inline bool operator<=(const adouble& lhs, const double rhs) { return lhs.getValue() <= rhs; }
    inline bool operator<=(const double lhs, const adouble& rhs) { return lhs <= rhs.getValue(); }
    inline bool operator>(const adouble& lhs, const double rhs) { return lhs.getValue() > rhs; }
    inline bool operator>(const double lhs, const adouble& rhs) { return lhs > rhs.getValue(); }
    inline bool operator>=(const adouble& lhs, const double rhs) { return lhs.getValue() >= rhs; }
    inline bool operator>=(const double lhs, const adouble& rhs) { return lhs >= rhs.getValue(); }

    inline bool operator==(const adouble& lhs, const int rhs) { return lhs == static_cast<double>(rhs); }
    inline bool operator==(const int lhs, const adouble& rhs) { return static_cast<double>(lhs) == rhs; }
    inline bool operator!=(const adouble& lhs, const int rhs) { return !(lhs == rhs); }
    inline bool operator!=(const int lhs, const adouble& rhs) { return !(lhs == rhs); }
    inline bool operator<(const adouble& lhs, const int rhs) { return lhs < static_cast<double>(rhs); }
    inline bool operator<(const int lhs, const adouble& rhs) { return static_cast<double>(lhs) < rhs; }
    inline bool operator<=(const adouble& lhs, const int rhs) { return lhs <= static_cast<double>(rhs); }
    inline bool operator<=(const int lhs, const adouble& rhs) { return static_cast<double>(lhs) <= rhs; }
    inline bool operator>(const adouble& lhs, const int rhs) { return lhs > static_cast<double>(rhs); }
    inline bool operator>(const int lhs, const adouble& rhs) { return static_cast<double>(lhs) > rhs; }
    inline bool operator>=(const adouble& lhs, const int rhs) { return lhs >= static_cast<double>(rhs); }
    inline bool operator>=(const int lhs, const adouble& rhs) { return static_cast<double>(lhs) >= rhs; }

    inline std::ostream& operator<<(std::ostream& os, const adouble& x)
    {
        os << x.getValue();
        return os;
    }

    inline adouble sqrt(const adouble& x)
    {
        const double value = std::sqrt(x.getValue());
        return makeUnaryResult(x, value, 0.5 / value);
    }

    inline adouble cbrt(const adouble& x)
    {
        const double value = std::pow(x.getValue(), 1.0 / 3.0);
        return makeUnaryResult(x, value, 1.0 / (3.0 * value * value));
    }

    inline adouble sin(const adouble& x)
    {
        return makeUnaryResult(x, std::sin(x.getValue()), std::cos(x.getValue()));
    }

    inline adouble cos(const adouble& x)
    {
        return makeUnaryResult(x, std::cos(x.getValue()), -std::sin(x.getValue()));
    }

    inline adouble tan(const adouble& x)
    {
        const double cos_x = std::cos(x.getValue());
        return makeUnaryResult(x, std::tan(x.getValue()), 1.0 / (cos_x * cos_x));
    }

    inline adouble asin(const adouble& x)
    {
        return makeUnaryResult(x, std::asin(x.getValue()), 1.0 / std::sqrt(1.0 - x.getValue() * x.getValue()));
    }

    inline adouble acos(const adouble& x)
    {
        return makeUnaryResult(x, std::acos(x.getValue()), -1.0 / std::sqrt(1.0 - x.getValue() * x.getValue()));
    }

    inline adouble atan(const adouble& x)
    {
        return makeUnaryResult(x, std::atan(x.getValue()), 1.0 / (1.0 + x.getValue() * x.getValue()));
    }

    inline adouble atan2(const adouble& y, const adouble& x)
    {
        const double denominator = x.getValue() * x.getValue() + y.getValue() * y.getValue();
        adouble result(std::atan2(y.getValue(), x.getValue()));

        const std::vector<size_t> y_indices = y.getDerivativeIndicies();
        const std::vector<size_t> x_indices = x.getDerivativeIndicies();
        std::vector<size_t> derivative_indices;
        derivative_indices.reserve(y_indices.size() + x_indices.size());
        derivative_indices.insert(derivative_indices.end(), y_indices.begin(), y_indices.end());
        derivative_indices.insert(derivative_indices.end(), x_indices.begin(), x_indices.end());
        std::sort(derivative_indices.begin(), derivative_indices.end());
        derivative_indices.erase(std::unique(derivative_indices.begin(), derivative_indices.end()), derivative_indices.end());

        for (std::vector<size_t>::const_iterator index = derivative_indices.begin();
             index != derivative_indices.end();
             ++index)
        {
            result.setDerivative(*index, (x.getValue() * y.getDerivative(*index) - y.getValue() * x.getDerivative(*index)) / denominator);
        }

        return result;
    }

    inline adouble exp(const adouble& x)
    {
        const double value = std::exp(x.getValue());
        return makeUnaryResult(x, value, value);
    }

    inline adouble log(const adouble& x)
    {
        return makeUnaryResult(x, std::log(x.getValue()), 1.0 / x.getValue());
    }

    inline adouble log10(const adouble& x)
    {
        return makeUnaryResult(x, std::log10(x.getValue()), 1.0 / (x.getValue() * std::log(10.0)));
    }

    inline adouble sinh(const adouble& x)
    {
        return makeUnaryResult(x, std::sinh(x.getValue()), std::cosh(x.getValue()));
    }

    inline adouble cosh(const adouble& x)
    {
        return makeUnaryResult(x, std::cosh(x.getValue()), std::sinh(x.getValue()));
    }

    inline adouble pow(const adouble& base, const double exponent)
    {
        return makeUnaryResult(base, std::pow(base.getValue(), exponent), exponent * std::pow(base.getValue(), exponent - 1.0));
    }

    inline adouble pow(const adouble& base, const int exponent)
    {
        return pow(base, static_cast<double>(exponent));
    }

    inline adouble pow(const double base, const adouble& exponent)
    {
        const double value = std::pow(base, exponent.getValue());
        return makeUnaryResult(exponent, value, value * std::log(base));
    }

    inline adouble pow(const adouble& base, const adouble& exponent)
    {
        return exp(exponent * log(base));
    }

    inline adouble abs(const adouble& x)
    {
        if (x.getValue() > 0.0)
            return x;
        else if (x.getValue() < 0.0)
            return -x;
        else
            return adouble(0.0);
    }

    inline adouble fabs(const adouble& x)
    {
        return abs(x);
    }

    inline adouble floor(const adouble& x)
    {
        return adouble(std::floor(x.getValue()));
    }

    inline adouble ceil(const adouble& x)
    {
        return adouble(std::ceil(x.getValue()));
    }
}
