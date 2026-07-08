#include "GSAD_2B.h"
#include "doubleType.h"
#include "EMTG_Matrix.h"

#include <cmath>
#include <iostream>
#include <functional>
#include <stdexcept>
#include <string>
#include <vector>

namespace
{
    void check_close(const std::string& label, const double actual, const double expected, const double tolerance)
    {
        if (std::fabs(actual - expected) > tolerance)
        {
            throw std::runtime_error(label + " expected " + std::to_string(expected) + " but got " + std::to_string(actual));
        }
    }

    void check_true(const std::string& label, const bool condition)
    {
        if (!condition)
            throw std::runtime_error(label);
    }

    double composite_function(const double x, const double y)
    {
        return x * y + std::sin(x) / std::exp(y) - std::sqrt(x + 2.0) + std::pow(x, 3.0);
    }

    double finite_difference_x(const double x, const double y)
    {
        const double step = 1.0e-6;
        return (composite_function(x + step, y) - composite_function(x - step, y)) / (2.0 * step);
    }

    double finite_difference_y(const double x, const double y)
    {
        const double step = 1.0e-6;
        return (composite_function(x, y + step) - composite_function(x, y - step)) / (2.0 * step);
    }

    double finite_difference_3d(const std::function<double(double, double, double)>& function,
                                const double x,
                                const double y,
                                const double z,
                                const size_t variable_index)
    {
        const double step = 1.0e-6;

        if (variable_index == 0)
            return (function(x + step, y, z) - function(x - step, y, z)) / (2.0 * step);
        else if (variable_index == 1)
            return (function(x, y + step, z) - function(x, y - step, z)) / (2.0 * step);
        else
            return (function(x, y, z + step) - function(x, y, z - step)) / (2.0 * step);
    }

    double elementary_function(const double x, const double y, const double z)
    {
        return std::tan(x)
             + std::asin(y)
             + std::acos(z)
             + std::atan(x * y)
             + std::log(x + 2.0)
             + std::log10(y + 2.0)
             + std::sinh(z)
             + std::cosh(x)
             - std::cbrt(y + 2.5)
             + std::pow(x + 2.0, y + 1.2)
             + std::pow(2.2, z)
             + std::fabs(z - 0.4);
    }

    GSAD::adouble elementary_function(const GSAD::adouble& x, const GSAD::adouble& y, const GSAD::adouble& z)
    {
        return tan(x)
             + asin(y)
             + acos(z)
             + atan(x * y)
             + log(x + 2.0)
             + log10(y + 2.0)
             + sinh(z)
             + cosh(x)
             - cbrt(y + 2.5)
             + pow(x + 2.0, y + 1.2)
             + pow(2.2, z)
             + fabs(z - 0.4);
    }

    EMTG::math::Matrix<GSAD::adouble> make_adouble_vector(const double x, const double y, const double z, const size_t first_derivative_index)
    {
        EMTG::math::Matrix<GSAD::adouble> vector(3, 1, 0.0);
        vector(0) = x;
        vector(1) = y;
        vector(2) = z;
        vector(0).setDerivative(first_derivative_index, 1.0);
        vector(1).setDerivative(first_derivative_index + 1, 1.0);
        vector(2).setDerivative(first_derivative_index + 2, 1.0);
        return vector;
    }
}

int main()
{
    try
    {
        GSAD::adouble x = 1.2;
        GSAD::adouble y = -0.7;
        x.setDerivative(0, 1.0);
        x.setDerivative(5, 2.0);
        y.setDerivative(0, 3.0);
        y.setDerivative(2, 4.0);

        GSAD::adouble z = x * y + sin(x) / exp(y) - sqrt(x + 2.0) + pow(x, 3.0);
        const double expected_value = composite_function(x.getValue(), y.getValue());
        const double dfdx = finite_difference_x(x.getValue(), y.getValue());
        const double dfdy = finite_difference_y(x.getValue(), y.getValue());

        check_close("composite value", z.getValue(), expected_value, 1.0e-12);
        check_close("composite derivative index 0", z.getDerivative(0), dfdx + 3.0 * dfdy, 1.0e-8);
        check_close("composite derivative index 2", z.getDerivative(2), 4.0 * dfdy, 1.0e-8);
        check_close("composite derivative index 5", z.getDerivative(5), 2.0 * dfdx, 1.0e-8);
        check_close("missing derivative", z.getDerivative(99), 0.0, 0.0);

        GSAD::adouble elementary_x = 0.35;
        GSAD::adouble elementary_y = 0.25;
        GSAD::adouble elementary_z = -0.2;
        elementary_x.setDerivative(0, 1.0);
        elementary_y.setDerivative(1, 1.0);
        elementary_z.setDerivative(2, 1.0);
        GSAD::adouble elementary_result = elementary_function(elementary_x, elementary_y, elementary_z);
        const std::function<double(double, double, double)> elementary_double =
            static_cast<double (*)(double, double, double)>(elementary_function);
        check_close("elementary function value",
                    elementary_result.getValue(),
                    elementary_function(elementary_x.getValue(), elementary_y.getValue(), elementary_z.getValue()),
                    1.0e-12);
        check_close("elementary derivative x",
                    elementary_result.getDerivative(0),
                    finite_difference_3d(elementary_double, elementary_x.getValue(), elementary_y.getValue(), elementary_z.getValue(), 0),
                    1.0e-8);
        check_close("elementary derivative y",
                    elementary_result.getDerivative(1),
                    finite_difference_3d(elementary_double, elementary_x.getValue(), elementary_y.getValue(), elementary_z.getValue(), 1),
                    1.0e-8);
        check_close("elementary derivative z",
                    elementary_result.getDerivative(2),
                    finite_difference_3d(elementary_double, elementary_x.getValue(), elementary_y.getValue(), elementary_z.getValue(), 2),
                    1.0e-8);

        GSAD::adouble theta = atan2(y, x);
        const double denominator = x.getValue() * x.getValue() + y.getValue() * y.getValue();
        const double dtheta_dx = -y.getValue() / denominator;
        const double dtheta_dy = x.getValue() / denominator;
        check_close("atan2 value", theta.getValue(), std::atan2(y.getValue(), x.getValue()), 1.0e-12);
        check_close("atan2 derivative index 0", theta.getDerivative(0), dtheta_dx + 3.0 * dtheta_dy, 1.0e-12);
        check_close("atan2 derivative index 2", theta.getDerivative(2), 4.0 * dtheta_dy, 1.0e-12);
        check_close("atan2 derivative index 5", theta.getDerivative(5), 2.0 * dtheta_dx, 1.0e-12);

        GSAD::adouble sparse = 10.0;
        sparse.setDerivative(7, 1.5);
        sparse.setDerivative(3, -2.0);
        sparse.setDerivative(7, 0.0);
        sparse.setValue(11.0);
        std::vector<size_t> indices = sparse.getDerivativeIndicies();
        check_true("sparse derivative index count", indices.size() == 1);
        check_true("sparse derivative sorted index", indices[0] == 3);
        check_close("setValue preserves derivatives", sparse.getDerivative(3), -2.0, 0.0);

        sparse = 4.0;
        check_close("scalar assignment clears derivatives", sparse.getDerivative(3), 0.0, 0.0);
        check_close("scalar assignment value", sparse.getValue(), 4.0, 0.0);

        GSAD::adouble cancellation = x - x;
        check_true("exact cancellation removes sparse derivatives", cancellation.getDerivativeIndicies().empty());

        check_close("abs derivative positive", abs(x).getDerivative(0), 1.0, 0.0);
        check_close("abs derivative negative", abs(y).getDerivative(2), -4.0, 0.0);
        check_true("comparison uses values", y < x && x > y && x != y);

        doubleType emtg_value = 3.0;
        emtg_value.setDerivative(4, 2.5);
        doubleType emtg_result = emtg_value * emtg_value;
        check_close("doubleType aliases adouble value", emtg_result.getValue(), 9.0, 0.0);
        check_close("doubleType aliases adouble derivative", emtg_result.getDerivative(4), 15.0, 0.0);

        EMTG::math::Matrix<GSAD::adouble> vector(2, 1, 0.0);
        vector(0) = x;
        vector(1) = y;
        GSAD::adouble vector_norm = vector.norm();
        const double expected_norm = std::sqrt(x.getValue() * x.getValue() + y.getValue() * y.getValue());
        check_close("Matrix<adouble> norm value", vector_norm.getValue(), expected_norm, 1.0e-12);
        check_close("Matrix<adouble> norm derivative index 0",
                    vector_norm.getDerivative(0),
                    (x.getValue() * x.getDerivative(0) + y.getValue() * y.getDerivative(0)) / expected_norm,
                    1.0e-12);
        check_close("Matrix<adouble> norm derivative index 2",
                    vector_norm.getDerivative(2),
                    (y.getValue() * y.getDerivative(2)) / expected_norm,
                    1.0e-12);

        EMTG::math::Matrix<GSAD::adouble> left = make_adouble_vector(1.1, -2.0, 0.7, 0);
        EMTG::math::Matrix<GSAD::adouble> right = make_adouble_vector(-0.4, 3.2, 1.5, 3);

        GSAD::adouble dot_product = left.dot(right);
        check_close("Matrix<adouble> dot value", dot_product.getValue(), 1.1 * -0.4 + -2.0 * 3.2 + 0.7 * 1.5, 1.0e-12);
        check_close("Matrix<adouble> dot derivative left x", dot_product.getDerivative(0), right(0).getValue(), 1.0e-12);
        check_close("Matrix<adouble> dot derivative left y", dot_product.getDerivative(1), right(1).getValue(), 1.0e-12);
        check_close("Matrix<adouble> dot derivative left z", dot_product.getDerivative(2), right(2).getValue(), 1.0e-12);
        check_close("Matrix<adouble> dot derivative right x", dot_product.getDerivative(3), left(0).getValue(), 1.0e-12);
        check_close("Matrix<adouble> dot derivative right y", dot_product.getDerivative(4), left(1).getValue(), 1.0e-12);
        check_close("Matrix<adouble> dot derivative right z", dot_product.getDerivative(5), left(2).getValue(), 1.0e-12);

        EMTG::math::Matrix<GSAD::adouble> cross_product = left.cross(right);
        check_close("Matrix<adouble> cross x value", cross_product(0).getValue(), left(1).getValue() * right(2).getValue() - left(2).getValue() * right(1).getValue(), 1.0e-12);
        check_close("Matrix<adouble> cross y value", cross_product(1).getValue(), left(2).getValue() * right(0).getValue() - left(0).getValue() * right(2).getValue(), 1.0e-12);
        check_close("Matrix<adouble> cross z value", cross_product(2).getValue(), left(0).getValue() * right(1).getValue() - left(1).getValue() * right(0).getValue(), 1.0e-12);
        check_close("Matrix<adouble> cross x d left y", cross_product(0).getDerivative(1), right(2).getValue(), 1.0e-12);
        check_close("Matrix<adouble> cross x d right z", cross_product(0).getDerivative(5), left(1).getValue(), 1.0e-12);
        check_close("Matrix<adouble> cross y d left x", cross_product(1).getDerivative(0), -right(2).getValue(), 1.0e-12);
        check_close("Matrix<adouble> cross y d right x", cross_product(1).getDerivative(3), left(2).getValue(), 1.0e-12);
        check_close("Matrix<adouble> cross z d left x", cross_product(2).getDerivative(0), right(1).getValue(), 1.0e-12);
        check_close("Matrix<adouble> cross z d right y", cross_product(2).getDerivative(4), left(0).getValue(), 1.0e-12);

        EMTG::math::Matrix<GSAD::adouble> unit_left = left.unitize();
        const double left_norm = std::sqrt(left(0).getValue() * left(0).getValue()
                                         + left(1).getValue() * left(1).getValue()
                                         + left(2).getValue() * left(2).getValue());
        for (size_t component = 0; component < 3; ++component)
        {
            check_close("Matrix<adouble> unitize value " + std::to_string(component),
                        unit_left(component).getValue(),
                        left(component).getValue() / (left_norm + 1.0e-20),
                        1.0e-12);
            for (size_t derivative_index = 0; derivative_index < 3; ++derivative_index)
            {
                const double kronecker_delta = component == derivative_index ? 1.0 : 0.0;
                const double expected_derivative = kronecker_delta / (left_norm + 1.0e-20)
                                                 - left(component).getValue() * left(derivative_index).getValue()
                                                 / ((left_norm + 1.0e-20) * (left_norm + 1.0e-20) * left_norm);
                check_close("Matrix<adouble> unitize derivative " + std::to_string(component) + "," + std::to_string(derivative_index),
                            unit_left(component).getDerivative(derivative_index),
                            expected_derivative,
                            1.0e-12);
            }
        }

        std::cout << "adouble tests passed" << std::endl;
        return 0;
    }
    catch (const std::exception& error)
    {
        std::cerr << error.what() << std::endl;
        return 1;
    }
}
