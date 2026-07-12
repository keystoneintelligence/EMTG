#include "ephemeris_reader.h"

#include <array>
#include <cmath>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

namespace
{
    constexpr double sentinel = -9.87654321e99;

    void require_close(const double actual, const double expected, const char* label)
    {
        if (std::abs(actual - expected) > 1.0e-12)
        {
            std::cerr << label << ": expected " << expected << ", got " << actual << '\n';
            throw std::runtime_error("ephemeris reader value mismatch");
        }
    }

    void require_guard(const double actual, const char* label)
    {
        if (actual != sentinel)
            throw std::runtime_error(std::string(label) + " was overwritten");
    }

    template<std::size_t Size>
    void require_values(const std::array<double, Size>& actual,
                        const std::array<double, Size>& expected,
                        const char* label)
    {
        for (std::size_t i = 0; i < Size; ++i)
            require_close(actual[i], expected[i], label);
    }

    class TestEphemerisReader : public EMTG::ephemeris_reader
    {
    public:
        void load_linear_samples()
        {
            this->dataRows = 3;
            this->data["epoch"] = {0.0, 1.0, 2.0};
            this->data["x(km)"] = {0.0, 2.0, 4.0};
            this->data["y(km)"] = {10.0, 14.0, 18.0};
            this->data["z(km)"] = {-3.0, -2.0, -1.0};
            this->data["vx(km/s)"] = {5.0, 4.0, 3.0};
            this->data["vy(km/s)"] = {0.0, 10.0, 20.0};
            this->data["vz(km/s)"] = {100.0, 104.0, 108.0};
            this->data["mass(kg)"] = {1000.0, 900.0, 800.0};
            this->fit_splines();
        }
    };
}

int run_tests()
{
    TestEphemerisReader reader;
    reader.load_linear_samples();

    const std::array<double, 7> expectedState =
        {1.0, 12.0, -2.5, 4.5, 5.0, 102.0, 950.0};
    const std::array<double, 7> expectedDerivative =
        {2.0, 4.0, 1.0, -1.0, 10.0, 4.0, -100.0};

    // Put guards immediately before and after the seven-value destination.
    // The historical StateArray + 7 bug overwrites the right-hand guard.
    std::array<double, 9> guardedState;
    guardedState.fill(sentinel);
    reader.get7State(0.5, guardedState.data() + 1);
    require_guard(guardedState.front(), "get7State left guard");
    require_guard(guardedState.back(), "get7State right guard");
    std::array<double, 7> actualState;
    for (std::size_t i = 0; i < actualState.size(); ++i)
        actualState[i] = guardedState[i + 1];
    require_values(actualState, expectedState, "seven-state layout");

    std::array<double, 9> guardedDerivative;
    guardedDerivative.fill(sentinel);
    reader.get7StateDerivative(0.5, guardedDerivative.data() + 1);
    require_guard(guardedDerivative.front(), "get7StateDerivative left guard");
    require_guard(guardedDerivative.back(), "get7StateDerivative right guard");
    std::array<double, 7> actualDerivative;
    for (std::size_t i = 0; i < actualDerivative.size(); ++i)
        actualDerivative[i] = guardedDerivative[i + 1];
    require_values(actualDerivative, expectedDerivative, "seven-derivative layout");

    // The combined API promises two adjacent seven-value blocks. In the old
    // implementation, mass at slot 6 remained untouched and the write to slot
    // 7 was immediately replaced by the first derivative.
    std::array<double, 16> guardedCombined;
    guardedCombined.fill(sentinel);
    reader.get7StateAndDerivative(0.5, guardedCombined.data() + 1);
    require_guard(guardedCombined.front(), "combined left guard");
    require_guard(guardedCombined.back(), "combined right guard");
    for (std::size_t i = 0; i < expectedState.size(); ++i)
    {
        require_close(guardedCombined[i + 1], expectedState[i], "combined state block");
        require_close(guardedCombined[i + 8], expectedDerivative[i], "combined derivative block");
    }

    bool rejected = false;
    try
    {
        reader.get7State(-1.0, actualState.data());
    }
    catch (const std::runtime_error&)
    {
        rejected = true;
    }
    if (!rejected)
        throw std::runtime_error("reader accepted an epoch outside its spline domain");

    std::cout << "Ephemeris reader state layout tests passed\n";
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
        std::cerr << "Ephemeris reader state layout test failure: " << error.what() << '\n';
        return 1;
    }
}
