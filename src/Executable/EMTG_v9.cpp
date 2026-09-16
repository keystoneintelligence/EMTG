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

//============================================================================
// Name        : EMTG_v9.cpp
// Author      : Jacob Englander
// Version     :
// Copyright   : 
// Description : Main launch function for EMTG_v9
// Description : EMTG_v9 is a generic optimizer that handles all mission types
//============================================================================

#include <algorithm>
#include <cctype>
#include <iostream>
#include <fstream>
#include <sstream>
#include <exception>
#include <unordered_map>
#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <iomanip>
#include <optional>
#include <vector>

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#else
#include <unistd.h>
#endif

#include "missionoptions.h"
#include "mission.h"
#include "chinchilla.h"
#include "EMTG_enums.h"

#include "LaunchVehicle.h"
#include "Spacecraft.h"
#include "LaunchVehicleOptionsFactory.h"
#include "SpacecraftOptionsFactory.h"

#include "file_utilities.h"

#include "universe.h"
#include "body.h"
#include "atmosphere.h"
#include "ExponentialAtmosphere.h"
#include "HarmonicGravityField.h"

#include "boost/lexical_cast.hpp"
#include "boost/ptr_container/ptr_vector.hpp"

#include "SpiceUsr.h"

#include "BodydeticConversions.h"
#include "EMTG_math.h"
#include "EMTG_Matrix.h"

#ifdef SPLINE_EPHEM
#include "SplineEphem_universe.h"
#endif

#ifndef EMTG_VERSION
#define EMTG_VERSION "unknown"
#endif

namespace
{
    constexpr int EXIT_UNEXPECTED = 1;
    constexpr int EXIT_CLI_OR_OPTIONS = 2;
    constexpr int EXIT_MISSING_DATA = 3;
    constexpr int EXIT_SOLVER = 4;
    constexpr const char* NAIF_PLANETARY_BSP_URL =
        "https://naif.jpl.nasa.gov/pub/naif/generic_kernels/spk/planets/";
    constexpr const char* NAIF_GENERIC_KERNEL_URL =
        "https://naif.jpl.nasa.gov/pub/naif/generic_kernels/";
    constexpr const char* NAIF_MISSION_KERNEL_URL =
        "https://naif.jpl.nasa.gov/pub/naif/";

    struct CommandLineOptions
    {
        std::string optionsFile;
        std::optional<std::filesystem::path> dataDirectory;
        std::optional<std::filesystem::path> outputDirectory;
        bool help = false;
        bool version = false;
        bool capabilities = false;
        bool doctor = false;
        bool pause = false;
    };

    void print_usage(std::ostream& stream)
    {
        stream << "Usage: EMTGv9 [options] OPTIONS_FILE\n\n"
               << "Options:\n"
               << "  --data-dir PATH    Root containing Universe and HardwareModels\n"
               << "  --output-dir PATH  Override the mission results directory\n"
               << "  --doctor           Check solver and runtime-data availability\n"
               << "  --capabilities     Print compiled solver capabilities as JSON\n"
               << "  --version          Print the EMTG version\n"
               << "  --pause            Wait for Enter before exiting\n"
               << "  -h, --help         Show this help\n";
    }

    CommandLineOptions parse_command_line(const int argc, char* argv[])
    {
        CommandLineOptions result;
        for (int index = 1; index < argc; ++index)
        {
            const std::string argument(argv[index]);
            if (argument == "-h" || argument == "--help")
                result.help = true;
            else if (argument == "--version")
                result.version = true;
            else if (argument == "--capabilities")
                result.capabilities = true;
            else if (argument == "--doctor")
                result.doctor = true;
            else if (argument == "--pause")
                result.pause = true;
            else if (argument == "--data-dir" || argument == "--output-dir")
            {
                if (++index >= argc)
                    throw std::invalid_argument(argument + " requires a path");
                if (argument == "--data-dir")
                    result.dataDirectory = std::filesystem::path(argv[index]);
                else
                    result.outputDirectory = std::filesystem::path(argv[index]);
            }
            else if (!argument.empty() && argument.front() == '-')
                throw std::invalid_argument("Unknown option: " + argument);
            else if (result.optionsFile.empty())
                result.optionsFile = argument;
            else
                throw std::invalid_argument("Only one OPTIONS_FILE may be provided");
        }
        return result;
    }

    std::filesystem::path executable_directory(char* argv0)
    {
#ifdef _WIN32
        std::vector<char> buffer(32768, '\0');
        const DWORD length = GetModuleFileNameA(nullptr, buffer.data(), static_cast<DWORD>(buffer.size()));
        if (length > 0 && length < buffer.size())
            return std::filesystem::path(std::string(buffer.data(), length)).parent_path();
#else
        std::vector<char> buffer(4096, '\0');
        const ssize_t length = readlink("/proc/self/exe", buffer.data(), buffer.size() - 1);
        if (length > 0)
            return std::filesystem::path(std::string(buffer.data(), static_cast<std::size_t>(length))).parent_path();
#endif
        return std::filesystem::absolute(argv0).parent_path();
    }

    bool is_data_root(const std::filesystem::path& root)
    {
        return std::filesystem::is_directory(root / "Universe")
            && std::filesystem::is_directory(root / "HardwareModels");
    }

    bool has_kernel_with_extension(const std::filesystem::path& directory,
                                   std::string extension)
    {
        std::transform(extension.begin(), extension.end(), extension.begin(),
            [](const unsigned char character) { return static_cast<char>(std::tolower(character)); });

        std::error_code error;
        std::filesystem::directory_iterator entry(directory, error);
        const std::filesystem::directory_iterator end;
        while (!error && entry != end)
        {
            if (entry->is_regular_file(error))
            {
                std::string candidate = entry->path().extension().string();
                std::transform(candidate.begin(), candidate.end(), candidate.begin(),
                    [](const unsigned char character) { return static_cast<char>(std::tolower(character)); });
                if (candidate == extension)
                    return true;
            }
            entry.increment(error);
        }
        return false;
    }

    void print_kernel_download_help(std::ostream& stream,
                                    const std::filesystem::path& kernelDirectory)
    {
        stream << "\nEMTG does not bundle large BSP or mission-specific SPICE kernels.\n"
               << "Download planetary BSP files: " << NAIF_PLANETARY_BSP_URL << '\n'
               << "Browse all generic kernels: " << NAIF_GENERIC_KERNEL_URL << '\n'
               << "Browse mission archives: " << NAIF_MISSION_KERNEL_URL << '\n';
        if (!kernelDirectory.empty())
            stream << "Place the downloaded kernels in: " << kernelDirectory.string() << '\n';
    }

    std::optional<std::filesystem::path> discover_data_root(
        const CommandLineOptions& commandLine,
        const std::filesystem::path& executableDirectory)
    {
        if (commandLine.dataDirectory)
        {
            const auto candidate = std::filesystem::absolute(*commandLine.dataDirectory);
            if (is_data_root(candidate))
                return candidate;
            return std::nullopt;
        }

        std::vector<std::filesystem::path> candidates;
        if (const char* environmentRoot = std::getenv("EMTG_DATA_DIR"))
            candidates.emplace_back(environmentRoot);
        candidates.push_back(executableDirectory / "data");
        candidates.push_back(executableDirectory / ".." / "share" / "emtg");
        candidates.push_back(executableDirectory / "..");
        candidates.push_back(std::filesystem::current_path());
#ifdef _WIN32
        candidates.emplace_back("C:/emtg");
#endif

        for (const auto& candidate : candidates)
        {
            std::error_code error;
            const auto normalized = std::filesystem::weakly_canonical(candidate, error);
            if (!error && is_data_root(normalized))
                return normalized;
        }
        return std::nullopt;
    }

    bool options_file_sets(const std::string& filename, const std::string& key)
    {
        std::ifstream stream(filename);
        std::string line;
        while (std::getline(stream, line))
        {
            std::istringstream tokens(line);
            std::string first;
            tokens >> first;
            if (!first.empty() && first.front() != '#' && first == key)
                return true;
        }
        return false;
    }

    void print_capabilities()
    {
        std::cout << "{\"ipopt\":"
#ifdef EMTG_ENABLE_IPOPT
                  << "true"
#else
                  << "false"
#endif
                  << ",\"snopt\":"
#ifdef EMTG_ENABLE_SNOPT
                  << "true"
#else
                  << "false"
#endif
                  << "}\n";
    }

    std::string timestamp()
    {
        const std::time_t now = std::chrono::system_clock::to_time_t(std::chrono::system_clock::now());
        std::tm local{};
#ifdef _WIN32
        localtime_s(&local, &now);
#else
        localtime_r(&now, &local);
#endif
        std::ostringstream stream;
        stream << std::put_time(&local, "%m%d%Y_%H%M%S");
        return stream.str();
    }
}

int main(int argc, char* argv[])
{
    CommandLineOptions commandLine;
    try
    {
        commandLine = parse_command_line(argc, argv);
    }
    catch (const std::exception& error)
    {
        std::cerr << "EMTG command-line error: " << error.what() << "\n\n";
        print_usage(std::cerr);
        return EXIT_CLI_OR_OPTIONS;
    }

    if (commandLine.help)
    {
        print_usage(std::cout);
        return 0;
    }
    if (commandLine.version)
    {
        std::cout << "EMTG " << EMTG_VERSION << '\n';
        return 0;
    }
    if (commandLine.capabilities)
    {
        print_capabilities();
        return 0;
    }

    const std::filesystem::path executableDirectory = executable_directory(argv[0]);
    const auto discoveredDataRoot = discover_data_root(commandLine, executableDirectory);
    if (commandLine.doctor)
    {
        std::cout << "EMTG executable: " << executableDirectory.string() << '\n';
        std::cout << "Compiled solvers: ";
        print_capabilities();
        if (discoveredDataRoot)
        {
            const std::filesystem::path kernelDirectory =
                *discoveredDataRoot / "Universe" / "ephemeris_files";
            const bool hasBsp = has_kernel_with_extension(kernelDirectory, ".bsp");
            const bool hasLeapSeconds = has_kernel_with_extension(kernelDirectory, ".tls");
            const bool hasPlanetaryConstants = has_kernel_with_extension(kernelDirectory, ".tpc");

            std::cout << "Core data directory: " << discoveredDataRoot->string() << '\n'
                      << "Kernel directory: " << kernelDirectory.string() << '\n'
                      << "BSP kernels: " << (hasBsp ? "found" : "not found") << '\n'
                      << "Leap-seconds kernel (.tls): " << (hasLeapSeconds ? "found" : "not found") << '\n'
                      << "Planetary constants kernel (.tpc): "
                      << (hasPlanetaryConstants ? "found" : "not found") << '\n';

            if (hasBsp && hasLeapSeconds && hasPlanetaryConstants)
            {
                std::cout << "Status: ready\n";
                return 0;
            }

            std::cout << "Status: action required - install the SPICE kernels needed by your mission.\n";
            print_kernel_download_help(std::cout, kernelDirectory);
            return EXIT_MISSING_DATA;
        }
        std::cout << "Data directory: not found\n"
                  << "Set EMTG_DATA_DIR or pass --data-dir with a directory containing Universe and HardwareModels.\n";
        print_kernel_download_help(std::cout, {});
        return EXIT_MISSING_DATA;
    }

    if (commandLine.optionsFile.empty())
    {
        std::cerr << "EMTG requires an OPTIONS_FILE.\n\n";
        print_usage(std::cerr);
        return EXIT_CLI_OR_OPTIONS;
    }

    std::cout << "EMTG starting\n";

    try
    {
        const std::string options_file_name = commandLine.optionsFile;
        std::cout << options_file_name << std::endl;

        EMTG::missionoptions options;
        try
        {
            options.parse_mission(options_file_name);
        }
        catch (const std::exception& error)
        {
            std::cerr << "EMTG options error: " << error.what() << '\n';
            return EXIT_CLI_OR_OPTIONS;
        }

        const bool explicitUniverse = options_file_sets(options_file_name, "universe_folder");
        const bool explicitHardware = options_file_sets(options_file_name, "HardwarePath");
        if (commandLine.dataDirectory || !explicitUniverse || !explicitHardware)
        {
            if (!discoveredDataRoot)
            {
                std::cerr << "EMTG runtime data was not found. Pass --data-dir or set EMTG_DATA_DIR.\n";
                return EXIT_MISSING_DATA;
            }
            if (commandLine.dataDirectory || !explicitUniverse)
                options.universe_folder = (*discoveredDataRoot / "Universe").string();
            if (commandLine.dataDirectory || !explicitHardware)
                options.HardwarePath = (*discoveredDataRoot / "HardwareModels").string();
        }

        if (!std::filesystem::is_directory(options.universe_folder)
            || !std::filesystem::is_directory(options.HardwarePath))
        {
            std::cerr << "EMTG runtime data is incomplete. Universe='" << options.universe_folder
                      << "', HardwareModels='" << options.HardwarePath << "'.\n";
            return EXIT_MISSING_DATA;
        }

        const std::filesystem::path kernelDirectory =
            std::filesystem::path(options.universe_folder) / "ephemeris_files";
        if (options.ephemeris_source >= 1)
        {
            const std::filesystem::path leapSeconds =
                kernelDirectory / options.SPICE_leap_seconds_kernel;
            const std::filesystem::path planetaryConstants =
                kernelDirectory / options.SPICE_reference_frame_kernel;
            const bool hasBsp = has_kernel_with_extension(kernelDirectory, ".bsp");
            const bool hasLeapSeconds = std::filesystem::is_regular_file(leapSeconds);
            const bool hasPlanetaryConstants = std::filesystem::is_regular_file(planetaryConstants);

            if (!hasBsp || !hasLeapSeconds || !hasPlanetaryConstants)
            {
                std::cerr << "EMTG cannot start this SPICE/SplineEphem mission because required kernels are missing.\n"
                          << "BSP kernels: " << (hasBsp ? "found" : "not found") << '\n'
                          << "Leap-seconds kernel: " << leapSeconds.string() << ' '
                          << (hasLeapSeconds ? "[found]" : "[not found]") << '\n'
                          << "Planetary constants kernel: " << planetaryConstants.string() << ' '
                          << (hasPlanetaryConstants ? "[found]" : "[not found]") << '\n';
                print_kernel_download_help(std::cerr, kernelDirectory);
                return EXIT_MISSING_DATA;
            }
        }

        if (commandLine.outputDirectory)
        {
            options.override_working_directory = true;
            options.forced_working_directory = std::filesystem::absolute(*commandLine.outputDirectory).string();
        }

        //configure the LaunchVehicleOptions and SpacecraftOptions objects
        EMTG::HardwareModels::LaunchVehicleOptions myLaunchVehicleOptions = EMTG::HardwareModels::CreateLaunchVehicleOptions(options);
        EMTG::HardwareModels::SpacecraftOptions mySpacecraftOptions = EMTG::HardwareModels::CreateSpacecraftOptions(options);

        //create a working directory for the problem
        {
            std::string root_directory, mission_subfolder;

            if (options.override_working_directory)
                root_directory = options.forced_working_directory;
            else
                root_directory = (std::filesystem::current_path() / "EMTG_v9_results").string();

            if (options.override_mission_subfolder)
                mission_subfolder = options.forced_mission_subfolder;
            else
            {
                mission_subfolder = options.mission_name + "_" + timestamp();
            }

            //define a new working directory
            options.working_directory = root_directory + "/" + mission_subfolder;

            //create the working directory
            try
            {
                std::filesystem::create_directories(options.working_directory);
            }
            catch (std::exception &e)
            {
                //std::cerr << "Error " << e.what() << ": Directory creation failed" << std::endl;
#ifdef _WIN32
                std::cout << "Perhaps the output directory path is too long?" << std::endl;
#endif
                throw;
            }

            //print the options file to the new directory
            options.write(options.working_directory + "//" + options.mission_name + ".emtgopt", !options.print_only_non_default_options);
            mySpacecraftOptions.write_output_file(options.working_directory + "//" + options.mission_name + ".emtg_spacecraftopt");
        } //end working directory creation and options file printing


        //load all ephemeris data if using SPICE
        std::vector<std::filesystem::path> SPICE_files_initial;
        std::vector<std::filesystem::path> SPICE_files_not_required;
        std::vector<std::filesystem::path> SPICE_files_required;
        std::vector<int> SPICE_bodies_required;
        std::string filestring;
        if (options.ephemeris_source >= 1)
        {
            //load all BSP files
            EMTG::file_utilities::get_all_files_with_extension(std::filesystem::path(options.universe_folder + "/ephemeris_files/"), ".bsp", SPICE_files_initial);

            for (size_t k = 0; k < SPICE_files_initial.size(); ++k)
            {
                filestring = options.universe_folder + "/ephemeris_files/" + SPICE_files_initial[k].string();
                furnsh_c(filestring.c_str());
                std::cout << filestring << std::endl;
            }

            //disable quit-on-SPICE-error so that we can see what happens if the leap second and/or frame kernels don't load properly
            erract_c((SpiceChar*)"SET", 100, (SpiceChar*)"RETURN");

            //SPICE reference frame kernel
            std::string leapsecondstring = options.universe_folder + "/ephemeris_files/" + options.SPICE_leap_seconds_kernel;
            std::string referenceframestring = options.universe_folder + "/ephemeris_files/" + options.SPICE_reference_frame_kernel;
            furnsh_c(leapsecondstring.c_str());
            furnsh_c(referenceframestring.c_str());

            //disable SPICE error printing. This is because we can, and will often, go off the edge of an ephemeris file.
            errprt_c((SpiceChar*)"SET", 100, (SpiceChar*)"NONE");

            SPICE_files_required = SPICE_files_initial;

            std::cout << "Completed loading SPICE kernels." << std::endl;
        }

        //create a picture of a chinchilla
        draw_chinchilla();

        //if SplineEphem is enabled, create an empty SplineEphem universe
#ifdef SPLINE_EPHEM
        std::vector< std::tuple<int, int, int, double> > SplineUniverse_keyList;

        SplineEphem::universe SplineUniverse(SplineUniverse_keyList);
#endif

        //create a vector of universes for each journey
        std::vector<EMTG::Astrodynamics::universe > TheUniverse;
        options.TU = 0;
        for (int j = 0; j < options.number_of_journeys; ++j)
        {
#ifdef SPLINE_EPHEM
            TheUniverse.push_back(EMTG::Astrodynamics::universe(j, options.universe_folder + "//" + options.Journeys[j].journey_central_body + ".emtg_universe", options, &SplineUniverse));
#else
            TheUniverse.push_back(EMTG::Astrodynamics::universe(j, options.universe_folder + "//" + options.Journeys[j].journey_central_body + ".emtg_universe", options));
#endif
            std::stringstream universenamestream;

            universenamestream << options.Journeys[j].journey_central_body + "_Journey_" << j << ".universe_output";

            if (TheUniverse[j].TU > options.TU)
                options.TU = TheUniverse[j].TU;
        }

        for (int j = 0; j < options.number_of_journeys; ++j)
        {
            if (j > 0)
            {
                TheUniverse[j - 1].set_nextUniverse(TheUniverse[j]);
            }
        }

        // create a hash map of harmonic fields
        // keys are unique combinations of <degree, order, field/grav file>
        // If all Journeys requiring a specific field all use the same degree, order, then only one field needs to be created and shared amongst all of them
        // If a different deg./ord. for the SAME field is required in two separate Journeys, then a new unique HarmonicGravityField will be created and stored
        std::map<std::tuple<std::string, size_t, size_t>, std::shared_ptr<EMTG::Astrodynamics::HarmonicGravityField>> gravity_field_map;
        for (size_t j = 0; j < options.number_of_journeys; ++j)
        {
            if (options.perturb_J2 && options.Journeys[j].perturb_central_body_gravity_harmonics)
            {
                throw std::invalid_argument("The global physics option perturb_J2 cannot be set if the perturb_central_body_gravity_harmonics option is set in any EMTG Journey. The latter is set in Journey " + options.Journeys[j].journey_name
                    + ". The perturb_J2 global setting will be deprecated in the future.\n");
            }

            if (options.Journeys[j].perturb_central_body_gravity_harmonics)
            {
                // get the gravity file
                std::string grav_file = options.Journeys[j].central_body_gravity_file;
                const std::filesystem::path grav_path = grav_file;
                std::string field_name = grav_path.filename().string();
                size_t degree = options.Journeys[j].central_body_gravity_degree;
                size_t order = options.Journeys[j].central_body_gravity_order;

                auto tup = std::make_tuple(field_name, degree, order);
                std::map<std::tuple<std::string, size_t, size_t>, std::shared_ptr<EMTG::Astrodynamics::HarmonicGravityField>>::const_iterator itr = gravity_field_map.find(tup);

                // If the harmonic field has not been registered in the hash map, then create it, and store it there, and in the Universe's CentralBody
                if (itr == gravity_field_map.end())
                {
                    TheUniverse[j].central_body.harmonic_gravity_field = std::make_shared<EMTG::Astrodynamics::HarmonicGravityField>(degree, order);
                    TheUniverse[j].central_body.harmonic_gravity_field->parseSTKgrvFile(grav_file);

                    gravity_field_map.insert(std::make_pair(tup, TheUniverse[j].central_body.harmonic_gravity_field));
                }
                else // The harmonic field does exist in the hash map, so just point the Universe's CentralBody to it
                {
                    TheUniverse[j].central_body.harmonic_gravity_field = itr->second;
                }                
            }
        }

		//now that we have a Universe vector, we can set the atmosphere object for each universe
		for (int j = 0; j < options.number_of_journeys; ++j)
		{
			try
			{
				if (options.Journeys[j].perturb_drag || 
                    (options.Journeys[j].phase_type == EMTG::PhaseType::ProbeEntryPhase && (options.Journeys[j].perturb_drag_probe_AEI_to_end || options.Journeys[j].perturb_drag_probe_separation_to_AEI))
                    )
				{
					// need to choose the correct kind of atmosphere to create based on journey option
					if (options.Journeys[j].AtmosphericDensityModelKey == "Exponential")
					{
						TheUniverse[j].TheAtmosphere = std::make_shared<EMTG::Astrodynamics::ExponentialAtmosphere>(j, options.Journeys[j].AtmosphericDensityModelDataFile, options);
					}
					else
					{
						std::cout << "Impermissible choice for AtmosphericDensityModelKey for Journey " << j << std::endl;
						throw std::exception();
					}
				}
				else
				{
					// we are not calculating drag, so just build a placeholder atmosphere that does nothing
                    //TheUniverse[j].TheAtmosphere = atmospheres.back();
				}
			}
			catch (std::exception &myError)
			{
				std::cout << "Failure with configuring TheAtmosphere." << std::endl;
				throw;
			}
		}

        //now that we have a Universe vector, we can use it to populate the SplineEphem::universe
        //add every body that will we used in the mission to the SplineUniverse
#ifdef SPLINE_EPHEM
        SplineUniverse_keyList.clear();
        try
        {
            //double earliest_possible_epoch = options.launch_window_open_date + options.Journeys.front().wait_time_bounds[0];
            //double latest_possible_epoch = options.latestPossibleEpoch * 86400.0;

            size_t number_of_journeys_to_spline = std::min(options.number_of_journeys, options.stop_after_journey + 1);
            for (size_t j = 0; j < number_of_journeys_to_spline; ++j)
            {
                std::vector<int> body_index_array;

                //first boundary point
                if (options.Journeys[j].departure_class != EMTG::BoundaryClass::FreePoint
                    && options.Journeys[j].departure_elements_frame != EMTG::ReferenceFrame::ObjectReferenced)
                {
                    if (options.Journeys[j].destination_list[0] > 0)
                    {
                        body_index_array.push_back(options.Journeys[j].destination_list[0] - 1);

                        // perform a consistency check on the body_index_array
                        if (body_index_array.back() >= TheUniverse[j].bodies.size())
                        {
                            throw std::invalid_argument("Body index " + std::to_string(options.Journeys[j].destination_list[0]) + " in destination list for Journey: " + options.Journeys[j].journey_name +
                                " does not appear in the Universe file: " + options.universe_folder + "//" + options.Journeys[j].journey_central_body + ".emtg_universe");
                        }
                    }
                }

                //last boundary point
                if (options.Journeys[j].arrival_class != EMTG::BoundaryClass::FreePoint
                    && options.Journeys[j].arrival_elements_frame != EMTG::ReferenceFrame::ObjectReferenced)
                {
                    if (options.Journeys[j].destination_list[1] > 0)
                    {
                        body_index_array.push_back(options.Journeys[j].destination_list[1] - 1);

                        // perform a consistency check on the body_index_array
                        if (body_index_array.back() >= TheUniverse[j].bodies.size())
                        {
                            throw std::invalid_argument("Body index " + std::to_string(options.Journeys[j].destination_list[1]) + " in destination list for Journey: " + options.Journeys[j].journey_name +
                                " does not appear in the Universe file: " + options.universe_folder + "//" + options.Journeys[j].journey_central_body + ".emtg_universe");
                        }
                    }
                }

                //sequence
                for (int body : options.Journeys[j].sequence)
                {
                    if (body > 0)
                    {
                        body_index_array.push_back(body - 1);

                        if (body >= TheUniverse[j].bodies.size())
                        {
                            throw std::invalid_argument("Body index " + std::to_string(body) + " in the sequence list for Journey: " + options.Journeys[j].journey_name +
                                " does not appear in the Universe file: " + options.universe_folder + "//" + options.Journeys[j].journey_central_body + ".emtg_universe");
                        }

                    }
                }

                //perturbation list
                if (options.perturb_thirdbody)
                {
                    for (size_t b = 0; b < TheUniverse[j].perturbation_menu.size(); ++b)
                    {
                        size_t pert_body = TheUniverse[j].perturbation_menu[b];

                        body_index_array.push_back(pert_body);

                        if (pert_body >= TheUniverse[j].bodies.size())
                        {
                            throw std::invalid_argument("Body index " + std::to_string(pert_body) + " in the perturbation body list for Journey: " + options.Journeys[j].journey_name +
                                " does not appear in the Universe file: " + options.universe_folder + "//" + options.Journeys[j].journey_central_body + ".emtg_universe");
                        }
                    }
                }

                //distance constraint list
                for (std::string& constraint : options.Journeys[j].PhaseDistanceConstraintDefinitions)
                {
                    std::vector<std::string> ConstraintDefinitionCell;
                    boost::split(ConstraintDefinitionCell,
                        constraint,
                        boost::is_any_of("_"),
                        boost::token_compress_on);

                    if (boost::to_lower_copy(ConstraintDefinitionCell[1]) != "cb")
                    {
                        int bodyIndex = std::stoi(ConstraintDefinitionCell[1]) - 1;

                        body_index_array.push_back(bodyIndex);
                    }
                }

				//boundary constraint list
				//some, but not all, require new splines
				for (std::string& constraint : options.Journeys[j].BoundaryConstraintDefinitions)
				{
					std::vector<std::string> ConstraintDefinitionCell;
					boost::split(ConstraintDefinitionCell,
						constraint,
						boost::is_any_of("_"),
						boost::token_compress_on);

					// distance constraint
					if (boost::to_lower_copy(ConstraintDefinitionCell[2]).find("distanceconstraint") < 1024)
					{
						if (boost::to_lower_copy(ConstraintDefinitionCell[3]) != "cb")
						{
							int bodyIndex = std::stoi(ConstraintDefinitionCell[3]) - 1;

							body_index_array.push_back(bodyIndex);
						}
					}

					// angular momentum reference angle
					if (boost::to_lower_copy(ConstraintDefinitionCell[2]).find("angularmomentumreferenceangle") < 1024)
					{
						if (boost::to_lower_copy(ConstraintDefinitionCell[3]) != "cb")
						{
							int bodyIndex = std::stoi(ConstraintDefinitionCell[3]) - 1;

							body_index_array.push_back(bodyIndex);
						}
					}

					// elevation from ground station
					if (boost::to_lower_copy(ConstraintDefinitionCell[2]).find("deticelevationfromgroundstation") < 1024)
					{
						if (boost::to_lower_copy(ConstraintDefinitionCell[3]) != "cb")
						{
							int bodyIndex = std::stoi(ConstraintDefinitionCell[3]) - 1;

							body_index_array.push_back(bodyIndex);
						}
					}

					// target body elevation
					if (boost::to_lower_copy(ConstraintDefinitionCell[2]).find("targetdeticelevation") < 1024)
					{
						if (boost::to_lower_copy(ConstraintDefinitionCell[3]) != "cb")
						{
							int bodyIndex = std::stoi(ConstraintDefinitionCell[3]) - 1;

							body_index_array.push_back(bodyIndex);
						}
					}

					// RBP angle
					if (boost::to_lower_copy(ConstraintDefinitionCell[2]).find("rbp") < 1024)
					{
						if (boost::to_lower_copy(ConstraintDefinitionCell[3]) != "cb")
						{
							int bodyIndex = std::stoi(ConstraintDefinitionCell[3]) - 1;

							body_index_array.push_back(bodyIndex);
						}
					}

					// RPB angle
					if (boost::to_lower_copy(ConstraintDefinitionCell[2]).find("rpb") < 1024)
					{
						if (boost::to_lower_copy(ConstraintDefinitionCell[3]) != "cb")
						{
							int bodyIndex = std::stoi(ConstraintDefinitionCell[3]) - 1;

							body_index_array.push_back(bodyIndex);
						}
					}

					// RPR angle
					if (boost::to_lower_copy(ConstraintDefinitionCell[2]).find("rpr") < 1024)
					{
						if (boost::to_lower_copy(ConstraintDefinitionCell[3]) != "cb")
						{
							int bodyIndex = std::stoi(ConstraintDefinitionCell[3]) - 1;

							body_index_array.push_back(bodyIndex);
						}

						if (boost::to_lower_copy(ConstraintDefinitionCell[4]) != "cb")
						{
							int bodyIndex = std::stoi(ConstraintDefinitionCell[4]) - 1;

							body_index_array.push_back(bodyIndex);
						}
					}

					// RRP angle
					if (boost::to_lower_copy(ConstraintDefinitionCell[2]).find("rrp") < 1024)
					{
						if (boost::to_lower_copy(ConstraintDefinitionCell[3]) != "cb")
						{
							int bodyIndex = std::stoi(ConstraintDefinitionCell[3]) - 1;

							body_index_array.push_back(bodyIndex);
						}

						if (boost::to_lower_copy(ConstraintDefinitionCell[4]) != "cb")
						{
							int bodyIndex = std::stoi(ConstraintDefinitionCell[4]) - 1;

							body_index_array.push_back(bodyIndex);
						}
					}
                    
                    // two-body rotating frame constraint
                    if (boost::to_lower_copy(ConstraintDefinitionCell[2]).find("stateintwobodyrotatingframe") < 1024)
					{
						if (boost::to_lower_copy(ConstraintDefinitionCell[4]) != "cb")
						{
							int bodyIndex = std::stoi(ConstraintDefinitionCell[4]) - 1;

							body_index_array.push_back(bodyIndex);
						}

						if (boost::to_lower_copy(ConstraintDefinitionCell[5]) != "cb")
						{
							int bodyIndex = std::stoi(ConstraintDefinitionCell[5]) - 1;

							body_index_array.push_back(bodyIndex);
						}
					}

					// velocity declination w/r/t/ any body constraint
					if (boost::to_lower_copy(ConstraintDefinitionCell[2]).find("velocitydeclinationanybody") < 1024)
					{
						if (boost::to_lower_copy(ConstraintDefinitionCell[3]) != "cb")
						{
							int bodyIndex = std::stoi(ConstraintDefinitionCell[3]) - 1;

							body_index_array.push_back(bodyIndex);
						}
					}
				}

				//maneuver constraint list
				//some, but not all, require new splines
				for (std::string& constraint : options.Journeys[j].ManeuverConstraintDefinitions)
				{
					std::vector<std::string> ConstraintDefinitionCell;
					boost::split(ConstraintDefinitionCell,
						constraint,
						boost::is_any_of("_"),
						boost::token_compress_on);

					// PSFB body-probe-thrust angle
					if (boost::to_lower_copy(ConstraintDefinitionCell[1]).find("bpt") < 1024)
					{
						if (boost::to_lower_copy(ConstraintDefinitionCell[2]) != "cb")
						{
							int bodyIndex = std::stoi(ConstraintDefinitionCell[2]) - 1;

							body_index_array.push_back(bodyIndex);
						}
					}
				}

                for (size_t b = 0; b < body_index_array.size(); ++b)
                {
                    //do we already have this body?
                    bool body_in_keylist = false;
                    for (size_t k = 0; k < SplineUniverse_keyList.size(); ++k)
                    {
                        if (std::get<0>(SplineUniverse_keyList[k]) == TheUniverse[j].bodies[body_index_array[b]].spice_ID
                            && std::get<1>(SplineUniverse_keyList[k]) == TheUniverse[j].central_body_SPICE_ID)
                        {
                            body_in_keylist = true;
                            break;
                        }
                    }

                    if (!body_in_keylist && body_index_array[b] >= 0)
                    {
                        SplineUniverse_keyList.push_back(std::make_tuple(
                            TheUniverse[j].bodies[body_index_array[b]].spice_ID,
                            TheUniverse[j].central_body_SPICE_ID,
                            options.SplineEphem_points_per_period,
                            TheUniverse[j].mu));
                    }
                }//end loop over bodies in the universe

                //is this universe's central body the sun? If not, let's add this body with respect to the sun. Let's add extra ephemeris points, too.
                if (!(TheUniverse[j].central_body_SPICE_ID == 10))
                {
                    bool body_in_keylist = false;
                    for (size_t k = 0; k < SplineUniverse_keyList.size(); ++k)
                    {
                        if (std::get<0>(SplineUniverse_keyList[k]) == TheUniverse[j].central_body_SPICE_ID
                            && std::get<1>(SplineUniverse_keyList[k]) == 10)
                        {
                            body_in_keylist = true;
                            break;
                        }
                    }

                    if (!body_in_keylist)
                    {
                        SplineUniverse_keyList.push_back(std::make_tuple(
                            TheUniverse[j].central_body_SPICE_ID,
                            10,
                            options.SplineEphem_non_central_body_sun_points_per_period,
                            1.32712440018e+11));
                    }
                }

                ////do we need to update the earliest or latest possible epoch?
                //if (options.Journeys[j].arrival_class == EMTG::BoundaryClass::FreePoint)
                //{
                //    earliest_possible_epoch = options.Journeys[j].arrival_elements_reference_epoch < earliest_possible_epoch ? options.Journeys[j].arrival_elements_reference_epoch : earliest_possible_epoch;
                //    latest_possible_epoch = options.Journeys[j].arrival_elements_reference_epoch > latest_possible_epoch ? options.Journeys[j].arrival_elements_reference_epoch : latest_possible_epoch;
                //}
                //if (options.Journeys[j].departure_class == EMTG::BoundaryClass::FreePoint && j == 0)
                //{
                //    earliest_possible_epoch = options.Journeys[j].departure_elements_reference_epoch < earliest_possible_epoch ? options.Journeys[j].departure_elements_reference_epoch : earliest_possible_epoch;
                //    latest_possible_epoch = options.Journeys[j].departure_elements_reference_epoch > latest_possible_epoch ? options.Journeys[j].departure_elements_reference_epoch : latest_possible_epoch;
                //}
            }

            double earliestPossibleEpoch = options.earliestPossibleEpoch * 86400.0;
            double latestPossibleEpoch = options.latestPossibleEpoch * 86400.0;

            if (options.SplineEphem_truncate_ephemeris_at_maximum_mission_epoch
                && latestPossibleEpoch < (options.launch_window_open_date + options.Journeys.front().wait_time_bounds[1] + options.total_flight_time_bounds[1]))
                latestPossibleEpoch = options.launch_window_open_date + options.Journeys.front().wait_time_bounds[1] + options.total_flight_time_bounds[1] * 86400.0;
            /*if (earliest_possible_epoch > options.earliestPossibleEpoch * 86400.0)
                earliest_possible_epoch = options.earliestPossibleEpoch * 86400.0;*/
            SplineUniverse.reinitialize(SplineUniverse_keyList,
                earliestPossibleEpoch - 10.0 * 86400.0,
                latestPossibleEpoch + 10.0 * 86400.0);
        }
        catch (std::exception &myError)
        {
            std::cout << "Failure while configuring SplineEphem." << std::endl;
            std::cout << myError.what() << std::endl;
            std::cout << "Submit this error message to the EMTG development team, along with your .emtgopt, .emtg_universe file(s), your hardware model files, any relevant ephemeris files, and which branch you are using. This information will allow us to properly help you." << std::endl;
            if (std::string(myError.what()).find("SPICE kernel pool") != std::string::npos)
            {
                print_kernel_download_help(std::cerr,
                    std::filesystem::path(options.universe_folder) / "ephemeris_files");
                return EXIT_MISSING_DATA;
            }
#ifndef BACKGROUND_MODE //macro overrides if statement
            std::cout << "Press enter to close window." << std::endl;
            std::cin.ignore();
#endif
            throw;
        }
#endif

        //*****************************************************************

        //assemble the mission
        options.description.clear();

        for (size_t j = 0; j < options.number_of_journeys; ++j)
        {
            std::vector<int> phase_targets = options.Journeys[j].sequence;
            options.Journeys[j].sequence.clear();

            options.Journeys[j].sequence.push_back(options.Journeys[j].destination_list[0]);

            if (j > 0) //if not the first journey, insert an underscore
                options.description.append("_");
            options.description.append(TheUniverse[j].central_body_name + "(");
            switch (options.Journeys[j].sequence[0])
            {
            case -1: //begin at SOI
            {
                options.description.append("s");
                break;
            }
            case 0: //begin at central body
            {
                options.description.append("c");
                break;
            }
            default:
                if (options.Journeys[j].departure_class == EMTG::BoundaryClass::FreePoint)
                {
                    options.description.append("f");
                }
                else
                {
                    options.description.append(TheUniverse[j].bodies[options.Journeys[j].sequence[0] - 1].short_name);
                }
            }

            //first, how many phases are there in the journey?
            for (size_t p = 0; p < options.Journeys[j].number_of_phases - 1; ++p)
            {
                size_t bodyIndex = phase_targets[p];
                if (bodyIndex > 0 && bodyIndex < (TheUniverse[j].size_of_flyby_menu / 2) + 1) //this is a legitimate flyby
                {
                    if (bodyIndex - 1 > TheUniverse[j].flyby_menu.size())
                    {
                        throw std::invalid_argument("ERROR: Journey " + std::to_string(j) + " phase " + std::to_string(p) + " body index " + std::to_string(bodyIndex)
                            + " exceeds size of flyby menu.");
                    }

                    //append the flyby
                    options.Journeys[j].sequence.push_back(phase_targets[p]);

                    //update the mission description
                    options.description.append(TheUniverse[j].bodies[TheUniverse[j].flyby_menu[options.Journeys[j].sequence.back() - 1]].short_name);
                }
            }

            options.Journeys[j].sequence.push_back(options.Journeys[j].destination_list[1]);


            switch (options.Journeys[j].sequence.back())
            {
            case -1: //begin at SOI
            {
                options.description.append("s");
                break;
            }
            case 0: //begin at central body
            {
                options.description.append("c");
                break;
            }
            default:
                if (options.Journeys[j].arrival_class == EMTG::BoundaryClass::FreePoint)
                {
                    options.description.append("f");
                }
                else
                {
                    options.description.append(TheUniverse[j].bodies[options.Journeys[j].sequence.back() - 1].short_name);
                }
            }
            options.description.append(")");

            options.Journeys[j].number_of_phases = options.Journeys[j].sequence.size() - 1;

        }

        //next, instantiate and optimize a problem object
        try
        {
            EMTG::HardwareModels::LaunchVehicle myLaunchVehicle(myLaunchVehicleOptions);
            EMTG::HardwareModels::Spacecraft mySpacecraft(mySpacecraftOptions);
            EMTG::Mission TrialMission(options, TheUniverse, myLaunchVehicle, mySpacecraft);

            //copy the appropriate trialX, if necessary
            if (options.run_inner_loop == EMTG::InnerLoopSolverType::RUN_TRIALX
                || options.run_inner_loop == EMTG::InnerLoopSolverType::NLP
                || (options.run_inner_loop == EMTG::InnerLoopSolverType::MBH && options.seed_MBH))
            {
                TrialMission.options.current_trialX.clear();
                for (size_t trialXindex = 0; trialXindex < options.trialX.size(); ++trialXindex)
                    TrialMission.options.current_trialX.push_back(std::get<1>(options.trialX[trialXindex]));
            }

            //evaluate the mission
            bool optimized_successfully = TrialMission.optimize();
            if (!optimized_successfully)
            {
                std::cerr << "EMTG solver did not produce a successful mission.\n";
                return EXIT_SOLVER;
            }

            //output the mission
            if (optimized_successfully)
            {
                //TrialMission.what_the_heck_am_I_called(EMTG::SolutionOutputType::SUCCESS);
                //TrialMission.output(options.outputfile);

                //output ephemeris file if desired
                if (options.generate_forward_integrated_ephemeris)
                    TrialMission.output_ephemeris();

                if (options.output_STMs)
                    TrialMission.output_STMs();

                if (options.output_maneuver_and_target_spec_files)
                    TrialMission.output_maneuver_and_target_spec();
            }
        }
        catch (std::exception &e)
        {
            std::cerr << "Error " << e.what() << ": Failure to run inner-loop solver" << std::endl;
            return EXIT_SOLVER;
        }

        //unload SPICE

        if (options.ephemeris_source >= 1)
        {
            for (size_t k = 0; k < SPICE_files_required.size(); ++k)
            {
                filestring = options.universe_folder + "ephemeris_files/" + SPICE_files_required[k].string();
                unload_c(filestring.c_str());
            }

            unload_c((options.universe_folder + "ephemeris_files/" + options.SPICE_leap_seconds_kernel).c_str());
            unload_c((options.universe_folder + "ephemeris_files/" + options.SPICE_reference_frame_kernel).c_str());
        }

        std::cout << "EMTG run complete." << std::endl;

        if (commandLine.pause)
        {
            std::cout << "Press enter to close window." << std::endl;
            std::cin.ignore();
        }
    }
    catch (std::exception &exception)
    {
        std::cout << "\nEMTG failed with error:" << std::endl;
        std::cout << exception.what() << std::endl;
        std::cout << "Submit this error message to the EMTG development team, along with your .emtgopt, .emtg_universe file(s), your hardware model files, any relevant ephemeris files, and which branch you are using. This information will allow us to properly help you." << std::endl;
        if (commandLine.pause)
        {
            std::cout << "Press enter to close window." << std::endl;
            std::cin.ignore();
        }
        return EXIT_UNEXPECTED;
    }

    return 0;
}
