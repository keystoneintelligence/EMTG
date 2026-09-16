// EMTG: Evolutionary Mission Trajectory Generator
// An open-source global optimization tool for preliminary mission design
// Provided by NASA Goddard Space Flight Center
//
// Copyright (c) 2013 - 2024 United States Government as represented by the
// Administrator of the National Aeronautics and Space Administration.
// All Other Rights Reserved.

#include "NLPInterfaceFactory.h"

#ifdef EMTG_ENABLE_SNOPT
#include "SNOPT_interface.h"
#endif

#ifdef EMTG_ENABLE_IPOPT
#include "IPOPT_interface.h"
#endif

#include <iostream>
#include <sstream>
#include <stdexcept>

namespace EMTG
{
    namespace Solvers
    {
        bool IsNLPSolverAvailable(const int solverType)
        {
            switch (solverType)
            {
            case 0:
#ifdef EMTG_ENABLE_SNOPT
                return true;
#else
                return false;
#endif
            case 2:
#ifdef EMTG_ENABLE_IPOPT
                return true;
#else
                return false;
#endif
            default:
                return false;
            }
        }

        std::vector<int> GetAvailableNLPSolverTypes()
        {
            std::vector<int> solverTypes;
            if (IsNLPSolverAvailable(0))
                solverTypes.push_back(0);
            if (IsNLPSolverAvailable(2))
                solverTypes.push_back(2);
            return solverTypes;
        }

        std::string GetAvailableNLPSolverDescription()
        {
            std::ostringstream description;
            const std::vector<int> solverTypes = GetAvailableNLPSolverTypes();
            for (size_t index = 0; index < solverTypes.size(); ++index)
            {
                if (index > 0)
                    description << ", ";
                description << (solverTypes[index] == 0 ? "SNOPT (0)" : "IPOPT (2)");
            }
            return description.str();
        }

        std::unique_ptr<NLP_interface> CreateNLPInterface(problem* myProblem,
            const NLPoptions& myOptions)
        {
            const int requestedSolver = myProblem->options.NLP_solver_type;

            if (requestedSolver == 1)
            {
                throw std::runtime_error("WORHP is deprecated and unsupported in this EMTG build.");
            }

#ifdef EMTG_ENABLE_SNOPT
            if (requestedSolver == 0)
            {
                return std::unique_ptr<NLP_interface>(new SNOPT_interface(myProblem, myOptions));
            }
#endif

#ifdef EMTG_ENABLE_IPOPT
            if (requestedSolver == 2)
            {
                return std::unique_ptr<NLP_interface>(new IPOPT_interface(myProblem, myOptions));
            }
#endif

            if (requestedSolver == 0)
            {
                throw std::runtime_error("NLP_solver_type=0 requested SNOPT, but this EMTG build does not include SNOPT support. Available solvers: " + GetAvailableNLPSolverDescription() + ".");
            }

            if (requestedSolver == 2)
            {
                throw std::runtime_error("NLP_solver_type=2 requested IPOPT, but this EMTG build does not include IPOPT support. Available solvers: " + GetAvailableNLPSolverDescription() + ".");
            }

            throw std::runtime_error("Unsupported NLP_solver_type " + std::to_string(requestedSolver) + ".");
        }
    }//end namespace Solvers
}//end namespace EMTG
