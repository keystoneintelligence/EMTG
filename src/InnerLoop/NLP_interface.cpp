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

//NLP interface abstract base class

#include "NLP_interface.h"

namespace EMTG
{
    namespace Solvers
    {
        //constructor
        EMTG::Solvers::NLP_interface::NLP_interface() :
            nX(1),
            nF(1),
            lastSolveStatus(NLPSolveStatus::NotRun),
            lastSolveReturnCode(0),
            lastSolveWasAcceptable(false),
            solverName("NLP")
        {}

        EMTG::Solvers::NLP_interface::NLP_interface(problem* myProblem_in,
            const NLPoptions& myOptions) :
            myProblem(myProblem_in),
            myOptions(myOptions),
            nX(myProblem->total_number_of_NLP_parameters),
            nF(myProblem->total_number_of_constraints),
            nG(myProblem->Gdescriptions.size()),
            nA(myProblem->Adescriptions.size()),
            J_NLP_incumbent(math::LARGE),
            feasibility_metric_NLP_incumbent(math::LARGE),
            lastSolveStatus(NLPSolveStatus::NotRun),
            lastSolveReturnCode(0),
            lastSolveWasAcceptable(false),
            solverName("NLP")
        {

            this->X0_scaled = std::vector<doubleType>(nX, 0.0);
            this->X0_unscaled = std::vector<doubleType>(nX, 0.0);
            this->X_scaled = std::vector<doubleType>(nX, 0.0);
            this->X_unscaled = std::vector<doubleType>(nX, 0.0);
            this->F = std::vector<doubleType>(nF, 0.0);
            this->G = std::vector<double>(nG, 0.0);
            this->X_NLP_incumbent_scaled = std::vector<doubleType>(nX, 0.0);
            this->X_NLP_incumbent_unscaled = std::vector<doubleType>(nX, 0.0);
            this->F_NLP_incumbent = std::vector<doubleType>(nF, 0.0);
            this->G_NLP_incumbent = std::vector<double>(nG, 0.0);
            this->Xupperbounds = std::vector<double>(this->nX);
            this->Xlowerbounds = std::vector<double>(this->nX);

            for (size_t Xindex = 0; Xindex < this->nX; ++Xindex)
            {
                this->Xupperbounds[Xindex] = this->myProblem->Xupperbounds[Xindex] / this->myProblem->X_scale_factors[Xindex];
                this->Xlowerbounds[Xindex] = this->myProblem->Xlowerbounds[Xindex] / this->myProblem->X_scale_factors[Xindex];
            }
            this->Fupperbounds = myProblem->Fupperbounds;
            this->Flowerbounds = myProblem->Flowerbounds;
            //if we are in filament finder mode, then we need to construct our own version of F, G, iGfun/jGvar that are not the same as the Problem's
            if (this->myOptions.get_SolverMode() == NLPMode::FilamentFinder)
            {
                //for now, let's use the same linear constraints
                this->A = myProblem->A;
                this->iAfun = myProblem->iAfun;
                this->jAvar = myProblem->jAvar;

                //the filament finding problem is effectively unconstrained
                this->nF = 1;
                this->nG = this->nX;
                this->iGfun.resize(this->nG, 0);
                this->jGvar.resize(this->nG, 1); //we have to make everybody influence the filament otherwise SNOPT gets confused, even if not really true
                this->Flowerbounds.resize(1, 0.0);
                this->Fupperbounds.resize(1, math::LARGE);

                for (size_t Gindex = 0; Gindex < this->nG; ++Gindex)
                {
                    this->iGfun[Gindex] = 0;
                    this->jGvar[Gindex] = Gindex;
                }

                //add critical inequality constraints
                this->myProblem->locate_filament_critical_inequality_constraints();
                for (size_t Findex = 0; Findex < this->myProblem->F_indices_of_filament_critical_inequality_constraints.size(); ++Findex)
                {
                    ++this->nF;
                    this->Flowerbounds.push_back(this->myProblem->Flowerbounds[this->myProblem->F_indices_of_filament_critical_inequality_constraints[Findex]]);
                    this->Fupperbounds.push_back(this->myProblem->Fupperbounds[this->myProblem->F_indices_of_filament_critical_inequality_constraints[Findex]]);

                    for (size_t Gindex = 0; Gindex < this->myProblem->iGfun.size(); ++Gindex)
                    {
                        if (this->myProblem->iGfun[Gindex] == this->myProblem->F_indices_of_filament_critical_inequality_constraints[Findex])
                        {
                            this->original_G_indices_of_filament_critical_inequality_constraints.push_back(Gindex);
                            ++this->nG;
                            this->iGfun.push_back(this->nF - 1);
                            this->jGvar.push_back(this->myProblem->jGvar[Gindex]);
                        }
                    }
                }

                //size F and G appropriately
                this->F.resize(this->nF, 1.0e+100);
                this->G.resize(this->nG, 0.0);
            }
            else
            {
                this->A = myProblem->A;
                this->iAfun = myProblem->iAfun;
                this->jAvar = myProblem->jAvar;
                this->iGfun = myProblem->iGfun;
                this->jGvar = myProblem->jGvar;
            }
        }

        void NLP_interface::reset_solver_state(const std::string& solverNameIn)
        {
            this->solverName = solverNameIn;
            this->lastSolveStatus = NLPSolveStatus::NotRun;
            this->lastSolveReturnCode = 0;
            this->lastSolveWasAcceptable = false;
            this->reset_evaluation_cache();
        }

        void NLP_interface::set_solver_status(const NLPSolveStatus& status, const int& returnCode, const bool& wasAcceptable)
        {
            this->lastSolveStatus = status;
            this->lastSolveReturnCode = returnCode;
            this->lastSolveWasAcceptable = wasAcceptable;
        }

        void NLP_interface::reset_evaluation_cache()
        {
            this->evaluationCacheIsValid = false;
            this->evaluationCacheIncludesDerivatives = false;
        }

        bool NLP_interface::evaluation_cache_matches_current_point(const bool& needG) const
        {
            if (!this->evaluationCacheIsValid
                || (needG && !this->evaluationCacheIncludesDerivatives)
                || this->evaluationCacheX.size() != this->X_unscaled.size())
            {
                return false;
            }

            for (size_t Xindex = 0; Xindex < this->X_unscaled.size(); ++Xindex)
            {
                if (this->evaluationCacheX[Xindex] != this->X_unscaled[Xindex] _GETVALUE)
                {
                    return false;
                }
            }

            return true;
        }

        void NLP_interface::store_evaluation_cache(const bool& needG)
        {
            if (this->evaluationCacheX.size() != this->X_unscaled.size())
            {
                this->evaluationCacheX.resize(this->X_unscaled.size());
            }

            for (size_t Xindex = 0; Xindex < this->X_unscaled.size(); ++Xindex)
            {
                this->evaluationCacheX[Xindex] = this->X_unscaled[Xindex] _GETVALUE;
            }

            this->evaluationCacheIsValid = true;
            this->evaluationCacheIncludesDerivatives = needG;
        }

        void NLP_interface::evaluate_current_point(const bool& needG)
        {
            if (this->evaluation_cache_matches_current_point(needG))
            {
                return;
            }

            if (this->myOptions.get_SolverMode() == NLPMode::FilamentFinder)
            {
                this->myProblem->evaluate(this->X_unscaled, this->myProblem->F, this->myProblem->G, needG);

                this->F.front() = 0.0;
                for (size_t Findex = 1; Findex < this->myProblem->total_number_of_constraints; ++Findex)
                {
                    if (this->myProblem->F_equality_or_inequality[Findex - 1])
                    {
                        this->F.front() += this->myProblem->F[Findex] * this->myProblem->F[Findex];
                    }
                }

                if (needG)
                {
                    for (size_t Gindex = 0; Gindex < this->nG; ++Gindex)
                    {
                        this->G[Gindex] = 0.0;
                    }

                    const size_t Problem_nG = this->myProblem->Gdescriptions.size();
                    for (size_t Gindex = 0; Gindex < Problem_nG; ++Gindex)
                    {
                        const size_t Findex = this->myProblem->iGfun[Gindex];
                        const size_t Xindex = this->myProblem->jGvar[Gindex];

                        if (Findex > 0 && this->myProblem->F_equality_or_inequality[Findex - 1])
                        {
                            this->G[Xindex] += 2.0 * this->myProblem->F[Findex] * this->myProblem->G[Gindex];
                        }
                    }
                }

                for (size_t Findex = 1; Findex <= this->myProblem->F_indices_of_filament_critical_inequality_constraints.size(); ++Findex)
                {
                    this->F[Findex] = this->myProblem->F[this->myProblem->F_indices_of_filament_critical_inequality_constraints[Findex - 1]];
                }

                if (needG)
                {
                    for (size_t Gindex = this->nX; Gindex < this->nG; ++Gindex)
                    {
                        this->G[Gindex] = this->myProblem->G[this->original_G_indices_of_filament_critical_inequality_constraints[Gindex - this->nX]];
                    }
                }
            }
            else
            {
                this->myProblem->evaluate(this->X_unscaled, this->F, this->G, needG);
            }

            this->store_evaluation_cache(needG);
        }
    }//end namespace Solvers
}//end namespace EMTG
