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
#include <stdexcept>

namespace EMTG
{
    namespace Solvers
    {
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

#ifndef EMTG_ENABLE_SNOPT
            if (requestedSolver == 0)
            {
                std::cout << "WARNING: NLP_solver_type=0 requested SNOPT, but this EMTG build does not include SNOPT. Falling back to IPOPT." << std::endl;
                return std::unique_ptr<NLP_interface>(new IPOPT_interface(myProblem, myOptions));
            }
#endif
#endif

            if (requestedSolver == 0)
            {
                throw std::runtime_error("NLP_solver_type=0 requested SNOPT, but this EMTG build does not include SNOPT support.");
            }

            if (requestedSolver == 2)
            {
                throw std::runtime_error("NLP_solver_type=2 requested IPOPT, but this EMTG build does not include IPOPT support.");
            }

            throw std::runtime_error("Unsupported NLP_solver_type " + std::to_string(requestedSolver) + ".");
        }
    }//end namespace Solvers
}//end namespace EMTG
