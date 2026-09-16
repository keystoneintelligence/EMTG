#include <filesystem>
#include <fstream>
#include <iostream>
#include <vector>
#include "../Utilities/file_utilities.h"

int main(int argc, char** argv)
{
    if (argc != 2) return 2;
    const fs::path directory(argv[1]);
    fs::create_directories(directory);
    // Last-loaded overlapping SPICE data wins. Creation and append order must
    // not make DE430 override DE442 on one filesystem but not another.
    for (const char* name : {"de442.bsp", "de430.bsp", "ignored.txt"})
        std::ofstream(directory / name).put('x');
    std::vector<fs::path> actual = {"z_existing.bsp"};
    EMTG::file_utilities::get_all_files_with_extension(directory, ".bsp", actual);
    const std::vector<fs::path> expected = {"de430.bsp", "de442.bsp", "z_existing.bsp"};
    for (const char* name : {"de442.bsp", "de430.bsp", "ignored.txt"})
        fs::remove(directory / name);
    if (actual != expected) {
        std::cerr << "Kernel discovery did not preserve lexical load precedence\n";
        return 1;
    }
    return 0;
}
