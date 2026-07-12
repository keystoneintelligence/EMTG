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

//header file for SplineEphem body
//this is where the real work gets done
//Jacob Englander 11-8-2016

#ifdef SPLINE_EPHEM
#ifndef SPLINEPHEM_BODY
#define SPLINEPHEM_BODY

#include "SpiceUsr.h"
#include "NaturalCubicSpline.h"

#include <vector>
#include <iostream>
#include <string>

namespace SplineEphem
{
    class body
    {
    public:
        //constructors
        body();
        body(const int& SPICE_ID,
            const int& reference_body_SPICE_ID,
            const double& reference_body_mu,
            const size_t& number_of_steps_per_period = 100,
            const double& tLowerBound = 30000.0*86400.0, 
            const double& tUpperBound = 100000.0*86400.0);
        //destructor
        ~body();

        //methods
        void reinitialize(const int& SPICE_ID,
            const int& reference_body_SPICE_ID,
            const double& reference_body_mu,
            const size_t& number_of_steps_per_period = 100,
            const double& tLowerBound = 30000.0*86400.0,
            const double& tUpperBound = 100000.0*86400.0);

        inline void getPosition(const double& epoch, double* PositionArray)
        {
            if (epoch > this->ephemeris_window_close || epoch < this->ephemeris_window_open)
            {
                throw std::runtime_error("SplineEphem cannot find body " + std::to_string(this->SPICE_ID) + " with respect to " + std::to_string(this->reference_body_SPICE_ID)
                    + " on epoch " + std::to_string(epoch / 86400.0) + ". Ephemeris window opens on MJD " + std::to_string(this->ephemeris_window_open / 86400.0) + " and closes on MJD "
                    + std::to_string(this->ephemeris_window_close / 86400.0)
                    + ". Place a breakpoint in " + std::string(__FILE__) + ", line " + std::to_string(__LINE__));
            }

            PositionArray[0] = this->Spline_x.evaluate(epoch);
            PositionArray[1] = this->Spline_y.evaluate(epoch);
            PositionArray[2] = this->Spline_z.evaluate(epoch);
        };

        inline void getVelocity(const double& epoch, double* VelocityArray)
        {
            if (epoch > this->ephemeris_window_close || epoch < this->ephemeris_window_open)
            {
                throw std::runtime_error("SplineEphem cannot find body " + std::to_string(this->SPICE_ID) + " with respect to " + std::to_string(this->reference_body_SPICE_ID)
                    + " on epoch " + std::to_string(epoch / 86400.0) + ". Ephemeris window opens on MJD " + std::to_string(this->ephemeris_window_open / 86400.0) + " and closes on MJD "
                    + std::to_string(this->ephemeris_window_close / 86400.0)
                    + ". Place a breakpoint in " + std::string(__FILE__) + ", line " + std::to_string(__LINE__));
            }

            VelocityArray[0] = this->Spline_xdot.evaluate(epoch);
            VelocityArray[1] = this->Spline_ydot.evaluate(epoch);
            VelocityArray[2] = this->Spline_zdot.evaluate(epoch);
        };

        void get6State(const double& epoch, double* StateArray);

        inline void getPositionDerivative(const double& epoch, double* PositionDerivativeArray)
        {
            if (epoch > this->ephemeris_window_close || epoch < this->ephemeris_window_open)
            {
                throw std::runtime_error("SplineEphem cannot find body " + std::to_string(this->SPICE_ID) + " with respect to " + std::to_string(this->reference_body_SPICE_ID)
                    + " on epoch " + std::to_string(epoch / 86400.0) + ". Ephemeris window opens on MJD " + std::to_string(this->ephemeris_window_open / 86400.0) + " and closes on MJD "
                    + std::to_string(this->ephemeris_window_close / 86400.0)
                    + ". Place a breakpoint in " + std::string(__FILE__) + ", line " + std::to_string(__LINE__));
            }

            PositionDerivativeArray[0] = this->Spline_x.derivative(epoch);
            PositionDerivativeArray[1] = this->Spline_y.derivative(epoch);
            PositionDerivativeArray[2] = this->Spline_z.derivative(epoch);
        };

        inline void getVelocityDerivative(const double& epoch, double* VelocityDerivativeArray)
        {
            if (epoch > this->ephemeris_window_close || epoch < this->ephemeris_window_open)
            {
                throw std::runtime_error("SplineEphem cannot find body " + std::to_string(this->SPICE_ID) + " with respect to " + std::to_string(this->reference_body_SPICE_ID)
                    + " on epoch " + std::to_string(epoch / 86400.0) + ". Ephemeris window opens on MJD " + std::to_string(this->ephemeris_window_open / 86400.0) + " and closes on MJD "
                    + std::to_string(this->ephemeris_window_close / 86400.0)
                    + ". Place a breakpoint in " + std::string(__FILE__) + ", line " + std::to_string(__LINE__));
            }

            VelocityDerivativeArray[0] = this->Spline_xdot.derivative(epoch);
            VelocityDerivativeArray[1] = this->Spline_ydot.derivative(epoch);
            VelocityDerivativeArray[2] = this->Spline_zdot.derivative(epoch);
        };

        void get6StateDerivative(const double& epoch, double* StateDerivativeArray);
        void get6StateAndDerivative(const double& epoch, double* StateAndDerivativeArray);

        inline int getSPICE_ID() 
        {
            return this->SPICE_ID;
        };

        inline int getReferenceBody_SPICE_ID()
        {
            return this->reference_body_SPICE_ID;
        };

        inline double getEphemerisWindowOpen() const { return this->ephemeris_window_open; }
        inline double getEphemerisWindowClose() const { return this->ephemeris_window_close; }

    private:
        //private methods, if applicable

        void initialize(const int& SPICE_ID,
            const int& reference_body_SPICE_ID,
            const double& reference_body_mu,
            const size_t& number_of_steps_per_period = 100,
            const double& tLowerBound = 30000.0*86400.0,
            const double& tUpperBound = 100000.0*86400.0);

        void getCoverageWindow();

        void getPeriodFromSPICE();

        void free();

        //*********fields

        //setup information
        int SPICE_ID;
        int reference_body_SPICE_ID;
        double reference_body_mu;
        double ephemeris_window_open;//in MJD, defined as JD - 2400000.5
        double ephemeris_window_close;//in MJD, defined as JD - 2400000.5
        double ephemeris_window_width;//in days
        double Period;
        double time_step_width;
        size_t number_of_periods;
        size_t number_of_steps;
        size_t number_of_steps_per_period;
        
        //underlying data
        std::vector<double> t;
        std::vector<double> x;
        std::vector<double> y;
        std::vector<double> z;
        std::vector<double> xdot;
        std::vector<double> ydot;
        std::vector<double> zdot;

        EMTG::math::NaturalCubicSpline Spline_x;
        EMTG::math::NaturalCubicSpline Spline_y;
        EMTG::math::NaturalCubicSpline Spline_z;
        EMTG::math::NaturalCubicSpline Spline_xdot;
        EMTG::math::NaturalCubicSpline Spline_ydot;
        EMTG::math::NaturalCubicSpline Spline_zdot;
    };//end class body
}//end namespace SplineEphem

#endif //SPLINEPHEM_BODY
#endif //#SPLINE_EPHEM
