// EMTG: Evolutionary Mission Trajectory Generator
// An open-source global optimization tool for preliminary mission design
// Provided by NASA Goddard Space Flight Center
//
// Copyright (c) 2013 - 2024 United States Government as represented by the
// Administrator of the National Aeronautics and Space Administration.
// All Other Rights Reserved.

// Licensed under the NASA Open Source License (the "License"); 
// You may not use this file except in compliance with the License. 
// You may obtain a copy of the License at:
// https://opensource.org/licenses/NASA-1.3
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either 
// express or implied.   See the License for the specific language
// governing permissions and limitations under the License.

//test driver for new missionoptions
//Jacob Englander 1/9/2019

#include "missionoptions.h"
#include "journeyoptions.h"

#include <cmath>
#include <fstream>
#include <iostream>
#include <exception>
#include <stdexcept>
#include <string>
#include <tuple>

namespace
{
    void check_true(const std::string& label, const bool condition)
    {
        if (!condition)
            throw std::runtime_error(label);
    }

    void check_equal(const std::string& label, const std::string& actual, const std::string& expected)
    {
        if (actual != expected)
            throw std::runtime_error(label + " expected '" + expected + "' but got '" + actual + "'");
    }

    void check_equal(const std::string& label, const size_t actual, const size_t expected)
    {
        if (actual != expected)
            throw std::runtime_error(label + " expected " + std::to_string(expected) + " but got " + std::to_string(actual));
    }

    void check_close(const std::string& label, const double actual, const double expected, const double tolerance)
    {
        if (std::fabs(actual - expected) > tolerance)
            throw std::runtime_error(label + " expected " + std::to_string(expected) + " but got " + std::to_string(actual));
    }

    void check_file_exists(const std::string& filename)
    {
        std::ifstream file(filename);
        check_true("expected output file to exist: " + filename, file.good());
    }

    std::string source_file(const std::string& relative_path)
    {
        return std::string(EMTG_SOURCE_DIR) + "/" + relative_path;
    }
}

int main(int argc, char* argv[])
{
    try
    {
        //startup stuff
        std::cout << "program starting" << std::endl;

        //parse the options file
        std::string options_file_name;
        if (argc == 1)
            options_file_name = "default.emtgopt";
        else if (argc == 2)
            options_file_name.assign(argv[1]);

        std::cout << options_file_name << std::endl;

        EMTG::missionoptions options(options_file_name);

        check_equal("default number_of_journeys", options.number_of_journeys, 1);
        check_equal("default Journeys size", options.Journeys.size(), 1);
        check_equal("default journey name", options.Journeys[0].journey_name, "default");
        check_close("journey maximum mass propagated", options.Journeys[0].maximum_mass, options.maximum_mass, 0.0);

        options.Journeys[0].trialX.push_back(std::make_tuple("p0synthetic_variable", 1.25));
        options.assemble_initial_guess();
        check_equal("assembled trialX size", options.trialX.size(), 1);
        check_equal("assembled trialX prefix", std::get<0>(options.trialX[0]), "j0p0synthetic_variable");
        check_close("assembled trialX value", std::get<1>(options.trialX[0]), 1.25, 0.0);

        const std::string regression_file = source_file("testatron/tests/output_options/outputoptions_frameICRF.emtgopt");
        EMTG::missionoptions regression_options(regression_file);
        check_equal("regression mission name", regression_options.mission_name, "outputoptions_frameICRF");
        check_equal("regression number_of_journeys", regression_options.number_of_journeys, 1);
        check_equal("regression Journeys size", regression_options.Journeys.size(), 1);
        check_equal("regression universe folder", regression_options.universe_folder, "C:/emtg/testatron/universe/");
        check_equal("regression HardwarePath", regression_options.HardwarePath, "C:/emtg/testatron/HardwareModels/");
        check_equal("regression forced working directory", regression_options.forced_working_directory, "C:/emtg/testatron/tests/output_options");
        check_equal("regression journey name", regression_options.Journeys[0].journey_name, "EM_journey");
        check_equal("regression destination count", regression_options.Journeys[0].destination_list.size(), 2);
        check_true("regression destination values", regression_options.Journeys[0].destination_list[0] == 3 && regression_options.Journeys[0].destination_list[1] == 4);
        check_true("regression phase type", static_cast<int>(regression_options.Journeys[0].phase_type) == 6);
        check_true("regression journey trialX present", !regression_options.Journeys[0].trialX.empty());
        check_true("regression master trialX assembled", !regression_options.trialX.empty());
        check_true("regression master trialX is journey-prefixed", std::get<0>(regression_options.trialX[0]).find("j0") == 0);

        const std::string roundtrip_file = "tests/missionoptions_roundtrip.emtgopt";
        regression_options.write(roundtrip_file, true);
        check_file_exists(roundtrip_file);

        EMTG::missionoptions reparsed_options(roundtrip_file);
        check_equal("roundtrip mission name", reparsed_options.mission_name, regression_options.mission_name);
        check_equal("roundtrip number_of_journeys", reparsed_options.number_of_journeys, regression_options.number_of_journeys);
        check_equal("roundtrip journey name", reparsed_options.Journeys[0].journey_name, regression_options.Journeys[0].journey_name);
        check_equal("roundtrip journey trialX size", reparsed_options.Journeys[0].trialX.size(), regression_options.Journeys[0].trialX.size());
        check_equal("roundtrip master trialX size", reparsed_options.trialX.size(), regression_options.trialX.size());
        check_equal("roundtrip forced working directory", reparsed_options.forced_working_directory, regression_options.forced_working_directory);

        std::cout << "missionoptions tests passed" << std::endl;
        return 0;
    }
    catch (const std::exception& myException)
    {
        std::cout << "Oops!" << std::endl;
        std::cout << myException.what() << std::endl;
        return 1;
    }
}

