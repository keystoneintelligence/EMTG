// EMTG: Evolutionary Mission Trajectory Generator
// An open-source global optimization tool for preliminary mission design
// Provided by NASA Goddard Space Flight Center
//
// Copyright (c) 2013 - 2024 United States Government as represented by the
// Administrator of the National Aeronautics and Space Administration.
// All Other Rights Reserved.

#pragma once

#include "NLP_interface.h"

#include <memory>
#include <string>
#include <vector>

namespace EMTG
{
    namespace Solvers
    {
        std::unique_ptr<NLP_interface> CreateNLPInterface(problem* myProblem,
            const NLPoptions& myOptions);
        bool IsNLPSolverAvailable(const int solverType);
        std::vector<int> GetAvailableNLPSolverTypes();
        std::string GetAvailableNLPSolverDescription();
    }//end namespace Solvers
}//end namespace EMTG
