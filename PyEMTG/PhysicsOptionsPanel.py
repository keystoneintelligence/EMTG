#EMTG: Evolutionary Mission Trajectory Generator
#An open-source global optimization tool for preliminary mission design
#Provided by NASA Goddard Space Flight Center
#
#Copyright (c) 2014 - 2024 United States Government as represented by the
#Administrator of the National Aeronautics and Space Administration.
#All Other Rights Reserved.
#
#Licensed under the NASA Open Source License (the "License"); 
#You may not use this file except in compliance with the License. 
#You may obtain a copy of the License at:
#https://opensource.org/license/nasa1-3-php
#Unless required by applicable law or agreed to in writing, software
#distributed under the License is distributed on an "AS IS" BASIS,
#WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either 
#express or implied.   See the License for the specific language
#governing permissions and limitations under the License.

import wx
import wx.adv
import wx.lib.scrolledpanel
import platform

from adaptive_integration_options import (
    ERROR_CONTROL_MODE_CHOICES,
    INTEGRATOR_TYPE_CHOICES,
    STM_ERROR_CONTROL_CHOICES,
    integrator_type_to_selection,
    selection_to_integrator_type,
    validate_adaptive_options,
)

class PhysicsOptionsPanel(wx.lib.scrolledpanel.ScrolledPanel):    
    def __init__(self, parent, missionoptions):
        self.missionoptions = missionoptions
        self.parent = parent

        wx.lib.scrolledpanel.ScrolledPanel.__init__(self, parent)

        ephemerisgrid = wx.FlexGridSizer(10,2,5,5)
        perturbgrid = wx.GridSizer(8,2,8,8)
        integratorgrid = wx.FlexGridSizer(cols=2, vgap=5, hgap=5)
        
        self.lblephemeris_source = wx.StaticText(self, -1, "Ephemeris Source")
        ephemeris_source_typestypes = ['Static','SPICE','SplineEphem']
        self.cmbephemeris_source = wx.ComboBox(self, -1, choices = ephemeris_source_typestypes, style=wx.CB_READONLY)

        self.lblSPICE_high_fidelity_derivatives = wx.StaticText(self, -1, "Use high-fidelity SPICE derivatives?")
        self.chkSPICE_high_fidelity_derivatives = wx.CheckBox(self, -1)

        self.lblSPICE_leap_seconds_kernel = wx.StaticText(self, -1, "Leap seconds kernel")
        self.txtSPICE_leap_seconds_kernel = wx.TextCtrl(self, -1, "SPICE_leap_seconds_kernel", size=(200,-1))

        self.lblSPICE_reference_frame_kernel = wx.StaticText(self, -1, "Frame kernel")
        self.txtSPICE_reference_frame_kernel = wx.TextCtrl(self, -1, "SPICE_reference_frame_kernel", size=(200,-1))

        self.lbluniverse_folder = wx.StaticText(self, -1, "Universe folder")
        self.txtuniverse_folder = wx.TextCtrl(self, -1, "universe_folder", size=(400,-1))
        self.btnGetNewUniverseFolder = wx.Button(self, -1, "...")
        self.btnSetDefaultUniverse = wx.Button(self, -1, "Default")
        UniverseButtonSizer = wx.BoxSizer(wx.HORIZONTAL)
        UniverseButtonSizer.AddMany([self.txtuniverse_folder, self.btnGetNewUniverseFolder, self.btnSetDefaultUniverse])

        self.lblSplineEphem_points_per_period = wx.StaticText(self, -1, "SplineEphem sample points per orbit period")
        self.txtSplineEphem_points_per_period = wx.TextCtrl(self, -1, "SplineEphem_points_per_period", size=(100,-1))

        self.lblSplineEphem_non_central_body_sun_points_per_period = wx.StaticText(self, -1, "SplineEphem sample points of the sun relative to the central body")
        self.txtSplineEphem_non_central_body_sun_points_per_period = wx.TextCtrl(self, -1, "SplineEphem_non_central_body_sun_points_per_period", size=(100,-1))

        self.lblSplineEphem_truncate_ephemeris_at_maximum_mission_epoch = wx.StaticText(self, -1, "Shorten SplineEphem to maximum mission epoch? (less memory but impedes MBH)")
        self.chkSplineEphem_truncate_ephemeris_at_maximum_mission_epoch = wx.CheckBox(self, -1)

        
        self.lblearliestPossibleEpoch = wx.StaticText(self, -1, "Earliest possible SplineEphem epoch")
        self.txtearliestPossibleEpoch = wx.TextCtrl(self, -1, "earliestPossibleEpoch")
        self.earliestPossibleEpochCalendar = wx.adv.CalendarCtrl(self, -1)
        self.earliestPossibleEpochCalendar.Bind(wx.EVT_KEY_DOWN, self.onTab)
        earliestcalendarbox = wx.BoxSizer(wx.HORIZONTAL)
        earliestcalendarbox.AddMany([self.txtearliestPossibleEpoch, self.earliestPossibleEpochCalendar])

        self.lbllatestPossibleEpoch = wx.StaticText(self, -1, "latest possible SplineEphem epoch")
        self.txtlatestPossibleEpoch = wx.TextCtrl(self, -1, "latestPossibleEpoch")
        self.latestPossibleEpochCalendar = wx.adv.CalendarCtrl(self, -1)
        self.latestPossibleEpochCalendar.Bind(wx.EVT_KEY_DOWN, self.onTab)
        latestcalendarbox = wx.BoxSizer(wx.HORIZONTAL)
        latestcalendarbox.AddMany([self.txtlatestPossibleEpoch, self.latestPossibleEpochCalendar])

        self.lblspiral_segments = wx.StaticText(self, -1, "Number of spiral segments")
        self.txtspiral_segments = wx.TextCtrl(self, -1, "spiral_segments")

        self.lblintegrator_tolerance = wx.StaticText(self, -1, "Legacy adaptive tolerance")
        self.txtintegrator_tolerance = wx.TextCtrl(self, -1, "integrator_tolerance")

        self.lblpropagatorType = wx.StaticText(self, -1, "Propagator type")
        propagatorType_choices = ["Keplerian", "Integrator"]
        self.cmbpropagatorType = wx.ComboBox(self, -1, choices=propagatorType_choices, style=wx.CB_READONLY)

        ephemerisgrid.AddMany([self.lblephemeris_source, self.cmbephemeris_source,
                              self.lblSPICE_high_fidelity_derivatives, self.chkSPICE_high_fidelity_derivatives,
                              self.lblSPICE_leap_seconds_kernel, self.txtSPICE_leap_seconds_kernel,
                              self.lblSPICE_reference_frame_kernel, self.txtSPICE_reference_frame_kernel,
                              self.lbluniverse_folder, UniverseButtonSizer,
                              self.lblSplineEphem_points_per_period, self.txtSplineEphem_points_per_period,
                              self.lblSplineEphem_non_central_body_sun_points_per_period, self.txtSplineEphem_non_central_body_sun_points_per_period,
                              self.lblSplineEphem_truncate_ephemeris_at_maximum_mission_epoch, self.chkSplineEphem_truncate_ephemeris_at_maximum_mission_epoch,
                              self.lblearliestPossibleEpoch, earliestcalendarbox,
                              self.lbllatestPossibleEpoch, latestcalendarbox])


        lblLeftTitle = wx.StaticText(self, -1, "Ephemeris settings")
        vboxleft = wx.BoxSizer(wx.VERTICAL)
        vboxleft.AddMany([lblLeftTitle, ephemerisgrid])

        font = self.GetFont()
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        lblLeftTitle.SetFont(font)

        lblBottomTitle = wx.StaticText(self, -1, "Spiral settings")
        lblBottomTitle.SetFont(font)

        self.lblintegratorType = wx.StaticText(self, -1, "Integrator type")
        self.cmbintegratorType = wx.ComboBox(self, -1, choices=INTEGRATOR_TYPE_CHOICES, style=wx.CB_READONLY)
        self.cmbintegratorType.SetToolTip("Adaptive integration is optional and remains under numerical/optimizer qualification. Fixed step is the default.")

        self.lblintegrator_error_control_mode = wx.StaticText(self, -1, "Adaptive error contract")
        self.cmbintegrator_error_control_mode = wx.ComboBox(self, -1, choices=ERROR_CONTROL_MODE_CHOICES, style=wx.CB_READONLY)
        self.lblintegrator_relative_tolerance = wx.StaticText(self, -1, "State relative tolerance")
        self.txtintegrator_relative_tolerance = wx.TextCtrl(self, -1)
        self.lblintegrator_absolute_tolerance_position = wx.StaticText(self, -1, "Position absolute tolerance (km)")
        self.txtintegrator_absolute_tolerance_position = wx.TextCtrl(self, -1)
        self.lblintegrator_absolute_tolerance_velocity = wx.StaticText(self, -1, "Velocity absolute tolerance (km/s)")
        self.txtintegrator_absolute_tolerance_velocity = wx.TextCtrl(self, -1)
        self.lblintegrator_absolute_tolerance_mass = wx.StaticText(self, -1, "Mass/propellant absolute tolerance (kg)")
        self.txtintegrator_absolute_tolerance_mass = wx.TextCtrl(self, -1)
        self.lblintegrator_absolute_tolerance_time = wx.StaticText(self, -1, "Epoch absolute tolerance (s)")
        self.txtintegrator_absolute_tolerance_time = wx.TextCtrl(self, -1)
        self.lblintegrator_absolute_tolerance_other = wx.StaticText(self, -1, "Auxiliary-state absolute tolerance")
        self.txtintegrator_absolute_tolerance_other = wx.TextCtrl(self, -1)
        self.lblintegrator_stm_error_control = wx.StaticText(self, -1, "STM error-control policy")
        self.cmbintegrator_stm_error_control = wx.ComboBox(self, -1, choices=STM_ERROR_CONTROL_CHOICES, style=wx.CB_READONLY)
        self.lblintegrator_stm_relative_tolerance = wx.StaticText(self, -1, "STM relative tolerance")
        self.txtintegrator_stm_relative_tolerance = wx.TextCtrl(self, -1)
        self.lblintegrator_stm_absolute_tolerance = wx.StaticText(self, -1, "STM base absolute tolerance")
        self.txtintegrator_stm_absolute_tolerance = wx.TextCtrl(self, -1)

        self.lblintegration_time_step_size = wx.StaticText(self, -1, "Integrator time step size (seconds)")
        self.txtintegration_time_step_size = wx.TextCtrl(self, -1, "integration_time_step_size")
        self.lblintegrator_initial_step_size = wx.StaticText(self, -1, "Adaptive initial step (s; 0 = maximum)")
        self.txtintegrator_initial_step_size = wx.TextCtrl(self, -1)
        self.lblintegrator_minimum_step_size = wx.StaticText(self, -1, "Adaptive minimum step (s; 0 = automatic)")
        self.txtintegrator_minimum_step_size = wx.TextCtrl(self, -1)
        self.lblintegrator_safety_factor = wx.StaticText(self, -1, "Adaptive safety factor")
        self.txtintegrator_safety_factor = wx.TextCtrl(self, -1)
        self.lblintegrator_minimum_step_scale = wx.StaticText(self, -1, "Minimum controller scale")
        self.txtintegrator_minimum_step_scale = wx.TextCtrl(self, -1)
        self.lblintegrator_maximum_step_scale = wx.StaticText(self, -1, "Maximum controller scale")
        self.txtintegrator_maximum_step_scale = wx.TextCtrl(self, -1)
        self.lblintegrator_rejection_limit = wx.StaticText(self, -1, "Consecutive rejection limit")
        self.txtintegrator_rejection_limit = wx.TextCtrl(self, -1)

        spiralgrid = wx.GridSizer(2,2,5,5)
        spiralgrid.AddMany([self.lblspiral_segments, self.txtspiral_segments])

        vboxspiral = wx.BoxSizer(wx.VERTICAL)
        vboxspiral.AddMany([lblBottomTitle, spiralgrid])

        integratorgrid.AddMany([self.lblintegrator_tolerance, self.txtintegrator_tolerance,
                                self.lblpropagatorType, self.cmbpropagatorType,
                                self.lblintegratorType, self.cmbintegratorType,
                                self.lblintegrator_error_control_mode, self.cmbintegrator_error_control_mode,
                                self.lblintegrator_relative_tolerance, self.txtintegrator_relative_tolerance,
                                self.lblintegrator_absolute_tolerance_position, self.txtintegrator_absolute_tolerance_position,
                                self.lblintegrator_absolute_tolerance_velocity, self.txtintegrator_absolute_tolerance_velocity,
                                self.lblintegrator_absolute_tolerance_mass, self.txtintegrator_absolute_tolerance_mass,
                                self.lblintegrator_absolute_tolerance_time, self.txtintegrator_absolute_tolerance_time,
                                self.lblintegrator_absolute_tolerance_other, self.txtintegrator_absolute_tolerance_other,
                                self.lblintegrator_stm_error_control, self.cmbintegrator_stm_error_control,
                                self.lblintegrator_stm_relative_tolerance, self.txtintegrator_stm_relative_tolerance,
                                self.lblintegrator_stm_absolute_tolerance, self.txtintegrator_stm_absolute_tolerance,
                                self.lblintegration_time_step_size, self.txtintegration_time_step_size,
                                self.lblintegrator_initial_step_size, self.txtintegrator_initial_step_size,
                                self.lblintegrator_minimum_step_size, self.txtintegrator_minimum_step_size,
                                self.lblintegrator_safety_factor, self.txtintegrator_safety_factor,
                                self.lblintegrator_minimum_step_scale, self.txtintegrator_minimum_step_scale,
                                self.lblintegrator_maximum_step_scale, self.txtintegrator_maximum_step_scale,
                                self.lblintegrator_rejection_limit, self.txtintegrator_rejection_limit])

        StateRepresentationgrid = wx.GridSizer(3,2,5,5)
        StateRepresentationChoices = ['Cartesian', 'SphericalRADEC', 'SphericalAZFPA', 'COE', 'MEE', "IncomingBplane", "OutgoingBplane", "IncomingBplaneRpTA", "OutgoingBplaneRpTA"]
        self.lblPeriapseBoundaryStateRepresentation = wx.StaticText(self, -1, "PeriapseBoundary state representation")
        self.cmbPeriapseBoundaryStateRepresentation = wx.ComboBox(self, -1, choices=StateRepresentationChoices, style=wx.CB_READONLY)
        self.lblParallelShootingStateRepresentation = wx.StaticText(self, -1, "Parallel shooting decision variable state representation")
        self.cmbParallelShootingStateRepresentation = wx.ComboBox(self, -1, choices=StateRepresentationChoices[0:5], style=wx.CB_READONLY) #parallel shooting can't use the asymptotic coordinate sets
        self.lblParallelShootingConstraintStateRepresentation = wx.StaticText(self, -1, "Parallel shooting constraint state representation")
        self.cmbParallelShootingConstraintStateRepresentation = wx.ComboBox(self, -1, choices=['Cartesian','same as encoded state representation'], style=wx.CB_READONLY)
        StateRepresentationgrid.AddMany([self.lblPeriapseBoundaryStateRepresentation, self.cmbPeriapseBoundaryStateRepresentation,
                                         self.lblParallelShootingStateRepresentation, self.cmbParallelShootingStateRepresentation,
                                         self.lblParallelShootingConstraintStateRepresentation, self.cmbParallelShootingConstraintStateRepresentation])
        lblStateRepresentationBottomTitle = wx.StaticText(self, -1, "State Representation settings")
        lblStateRepresentationBottomTitle.SetFont(font)
        vboxStateRepresentation = wx.BoxSizer(wx.VERTICAL)
        vboxStateRepresentation.AddMany([lblStateRepresentationBottomTitle, StateRepresentationgrid])

        lblRightTitle = wx.StaticText(self, -1, "Perturbation settings")
        lblRightTitle.SetFont(font)
        vboxright = wx.BoxSizer(wx.VERTICAL)
        vboxright.AddMany([lblRightTitle, perturbgrid])

        self.lblperturb_SRP = wx.StaticText(self, -1, "Enable SRP")
        self.chkperturb_SRP = wx.CheckBox(self, -1)

        self.lblperturb_thirdbody = wx.StaticText(self, -1, "Enable third body")
        self.chkperturb_thirdbody = wx.CheckBox(self, -1)

        self.lblperturb_J2 = wx.StaticText(self, -1, "Enable central-body J2")
        self.chkperturb_J2 = wx.CheckBox(self, -1)

        self.lblspacecraft_area = wx.StaticText(self, -1, "Spacecraft area (in m^2)")
        self.txtspacecraft_area = wx.TextCtrl(self, -1, "spacecraft_area")

        self.lblcoefficient_of_reflectivity = wx.StaticText(self, -1, "Coefficient of reflectivity")
        self.txtcoefficient_of_reflectivity = wx.TextCtrl(self, -1, "coefficient_of_reflectivity")

        self.lblsolar_percentage = wx.StaticText(self, -1, "Solar percentage [0, 1]")
        self.txtsolar_percentage = wx.TextCtrl(self, -1, "solar_percentage")

        self.lblsolar_flux = wx.StaticText(self, -1, "Solar constant (flux at 1 AU)")
        self.txtsolar_flux = wx.TextCtrl(self, -1, "solar_flux")

        self.lblspeed_of_light_vac = wx.StaticText(self, -1, "Speed of light in a vacuum (m/s)")
        self.txtspeed_of_light_vac = wx.TextCtrl(self, -1, "speed_of_light_vac")

        perturbgrid.AddMany([ self.lblperturb_SRP, self.chkperturb_SRP,
                              self.lblperturb_thirdbody, self.chkperturb_thirdbody,
                              self.lblperturb_J2, self.chkperturb_J2,
                              self.lblspacecraft_area, self.txtspacecraft_area,
                              self.lblcoefficient_of_reflectivity, self.txtcoefficient_of_reflectivity,
                              self.lblsolar_percentage, self.txtsolar_percentage,
                              self.lblsolar_flux, self.txtsolar_flux,
                              self.lblspeed_of_light_vac, self.txtspeed_of_light_vac
                              ])
        self.mainbox = wx.BoxSizer(wx.HORIZONTAL)
        
        self.mainbox.Add(vboxleft)
        self.mainbox.AddSpacer(20)
        self.mainbox.Add(vboxright)

        self.mainvbox = wx.BoxSizer(wx.VERTICAL)
        self.mainvbox.Add(self.mainbox)
        self.mainvbox.AddSpacer(20)
        self.mainvbox.AddMany([vboxspiral, integratorgrid, vboxStateRepresentation])

        self.SetSizer(self.mainvbox)
        self.SetupScrolling()

        #bindings
        self.cmbephemeris_source.Bind(wx.EVT_COMBOBOX,self.Changeephemeris_source)
        self.chkSPICE_high_fidelity_derivatives.Bind(wx.EVT_CHECKBOX, self.ChangeSPICE_high_fidelity_derivatives)
        self.txtSPICE_leap_seconds_kernel.Bind(wx.EVT_KILL_FOCUS,self.ChangeSPICE_leap_seconds_kernel)
        self.txtSPICE_reference_frame_kernel.Bind(wx.EVT_KILL_FOCUS,self.ChangeSPICE_reference_frame_kernel)
        self.txtuniverse_folder.Bind(wx.EVT_KILL_FOCUS,self.Changeuniverse_folder)
        self.txtSplineEphem_points_per_period.Bind(wx.EVT_KILL_FOCUS, self.ChangeSplineEphem_points_per_period)
        self.txtSplineEphem_non_central_body_sun_points_per_period.Bind(wx.EVT_KILL_FOCUS, self.ChangeSplineEphem_non_central_body_sun_points_per_period)
        self.chkSplineEphem_truncate_ephemeris_at_maximum_mission_epoch.Bind(wx.EVT_CHECKBOX, self.ChangeSplineEphem_truncate_ephemeris_at_maximum_mission_epoch)
        self.txtearliestPossibleEpoch.Bind(wx.EVT_KILL_FOCUS,self.ChangeearliestPossibleEpoch)
        self.earliestPossibleEpochCalendar.Bind(wx.adv.EVT_CALENDAR_SEL_CHANGED, self.ChangeearliestPossibleEpochCalendar)
        self.txtlatestPossibleEpoch.Bind(wx.EVT_KILL_FOCUS,self.ChangelatestPossibleEpoch)
        self.latestPossibleEpochCalendar.Bind(wx.adv.EVT_CALENDAR_SEL_CHANGED, self.ChangelatestPossibleEpochCalendar)
        self.btnGetNewUniverseFolder.Bind(wx.EVT_BUTTON,self.GetNewUniverseFolder)
        self.btnSetDefaultUniverse.Bind(wx.EVT_BUTTON,self.SetDefaultUniverse)
        self.chkperturb_SRP.Bind(wx.EVT_CHECKBOX,self.Changeperturb_SRP)
        self.chkperturb_thirdbody.Bind(wx.EVT_CHECKBOX,self.Changeperturb_thirdbody)
        self.chkperturb_J2.Bind(wx.EVT_CHECKBOX,self.Changeperturb_J2)
        self.txtspacecraft_area.Bind(wx.EVT_KILL_FOCUS,self.Changespacecraft_area)
        self.txtcoefficient_of_reflectivity.Bind(wx.EVT_KILL_FOCUS,self.Changecoefficient_of_reflectivity)
        self.txtsolar_percentage.Bind(wx.EVT_KILL_FOCUS,self.Changesolar_percentage)
        self.txtsolar_flux.Bind(wx.EVT_KILL_FOCUS,self.Changesolar_flux)
        self.txtspeed_of_light_vac.Bind(wx.EVT_KILL_FOCUS,self.Changespeed_of_light_vac)
        self.txtspiral_segments.Bind(wx.EVT_KILL_FOCUS, self.Changespiral_segments)
        self.txtintegrator_tolerance.Bind(wx.EVT_KILL_FOCUS, self.ChangeIntegratorTolerance)
        self.cmbpropagatorType.Bind(wx.EVT_COMBOBOX, self.ChangepropagatorType)
        self.cmbintegratorType.Bind(wx.EVT_COMBOBOX, self.ChangeintegratorType)
        self.cmbintegrator_error_control_mode.Bind(wx.EVT_COMBOBOX, self.Changeintegrator_error_control_mode)
        self.cmbintegrator_stm_error_control.Bind(wx.EVT_COMBOBOX, self.Changeintegrator_stm_error_control)
        self.txtintegration_time_step_size.Bind(wx.EVT_KILL_FOCUS, self.Changeintegration_time_step_size)
        for control in [self.txtintegrator_relative_tolerance,
                        self.txtintegrator_absolute_tolerance_position,
                        self.txtintegrator_absolute_tolerance_velocity,
                        self.txtintegrator_absolute_tolerance_mass,
                        self.txtintegrator_absolute_tolerance_time,
                        self.txtintegrator_absolute_tolerance_other,
                        self.txtintegrator_stm_relative_tolerance,
                        self.txtintegrator_stm_absolute_tolerance,
                        self.txtintegrator_initial_step_size,
                        self.txtintegrator_minimum_step_size,
                        self.txtintegrator_safety_factor,
                        self.txtintegrator_minimum_step_scale,
                        self.txtintegrator_maximum_step_scale,
                        self.txtintegrator_rejection_limit]:
            control.Bind(wx.EVT_KILL_FOCUS, self.ChangeAdaptiveIntegratorSetting)
        self.cmbPeriapseBoundaryStateRepresentation.Bind(wx.EVT_COMBOBOX, self.ChangePeriapseBoundaryStateRepresentation)
        self.cmbParallelShootingStateRepresentation.Bind(wx.EVT_COMBOBOX, self.ChangeParallelShootingStateRepresentation)
        self.cmbParallelShootingConstraintStateRepresentation.Bind(wx.EVT_COMBOBOX, self.ChangeParallelShootingConstraintStateRepresentation)

    def update(self):

        self.cmbephemeris_source.SetSelection(self.missionoptions.ephemeris_source)
        self.txtSPICE_leap_seconds_kernel.SetValue(str(self.missionoptions.SPICE_leap_seconds_kernel))
        self.txtSPICE_reference_frame_kernel.SetValue(str(self.missionoptions.SPICE_reference_frame_kernel))
        self.txtuniverse_folder.SetValue(self.missionoptions.universe_folder)
        self.chkSPICE_high_fidelity_derivatives.SetValue(self.missionoptions.SPICE_high_fidelity_derivatives)
        self.txtSplineEphem_points_per_period.SetValue(str(self.missionoptions.SplineEphem_points_per_period))
        self.txtSplineEphem_non_central_body_sun_points_per_period.SetValue(str(self.missionoptions.SplineEphem_non_central_body_sun_points_per_period))
        self.chkSplineEphem_truncate_ephemeris_at_maximum_mission_epoch.SetValue(self.missionoptions.SplineEphem_truncate_ephemeris_at_maximum_mission_epoch)
        self.txtearliestPossibleEpoch.SetValue(str(self.missionoptions.earliestPossibleEpoch))
        earliestPossibleDate = wx.DateTime.FromJDN(self.missionoptions.earliestPossibleEpoch + 2400000.5)
        self.earliestPossibleEpochCalendar.SetDate(earliestPossibleDate.MakeUTC())        
        self.txtlatestPossibleEpoch.SetValue(str(self.missionoptions.latestPossibleEpoch))
        latestPossibleDate = wx.DateTime.FromJDN(self.missionoptions.latestPossibleEpoch + 2400000.5)
        self.latestPossibleEpochCalendar.SetDate(latestPossibleDate.MakeUTC())
        self.chkperturb_SRP.SetValue(self.missionoptions.perturb_SRP)
        self.chkperturb_thirdbody.SetValue(self.missionoptions.perturb_thirdbody)
        self.chkperturb_J2.SetValue(self.missionoptions.perturb_J2)
        self.txtspacecraft_area.SetValue(str(self.missionoptions.spacecraft_area))
        self.txtcoefficient_of_reflectivity.SetValue(str(self.missionoptions.coefficient_of_reflectivity))
        self.txtsolar_percentage.SetValue(str(self.missionoptions.solar_percentage))
        self.txtsolar_flux.SetValue(str(self.missionoptions.solar_flux))
        self.txtspeed_of_light_vac.SetValue(str(self.missionoptions.speed_of_light_vac))
        self.txtspiral_segments.SetValue(str(self.missionoptions.spiral_segments))
        self.txtintegrator_tolerance.SetValue(str(self.missionoptions.integrator_tolerance))
        self.cmbpropagatorType.SetSelection(self.missionoptions.propagatorType)
        self.cmbintegratorType.SetSelection(integrator_type_to_selection(self.missionoptions.integratorType))
        self.cmbintegrator_error_control_mode.SetSelection(self.missionoptions.integrator_error_control_mode)
        self.txtintegrator_relative_tolerance.SetValue(str(self.missionoptions.integrator_relative_tolerance))
        self.txtintegrator_absolute_tolerance_position.SetValue(str(self.missionoptions.integrator_absolute_tolerance_position))
        self.txtintegrator_absolute_tolerance_velocity.SetValue(str(self.missionoptions.integrator_absolute_tolerance_velocity))
        self.txtintegrator_absolute_tolerance_mass.SetValue(str(self.missionoptions.integrator_absolute_tolerance_mass))
        self.txtintegrator_absolute_tolerance_time.SetValue(str(self.missionoptions.integrator_absolute_tolerance_time))
        self.txtintegrator_absolute_tolerance_other.SetValue(str(self.missionoptions.integrator_absolute_tolerance_other))
        self.cmbintegrator_stm_error_control.SetSelection(self.missionoptions.integrator_stm_error_control)
        self.txtintegrator_stm_relative_tolerance.SetValue(str(self.missionoptions.integrator_stm_relative_tolerance))
        self.txtintegrator_stm_absolute_tolerance.SetValue(str(self.missionoptions.integrator_stm_absolute_tolerance))
        self.txtintegration_time_step_size.SetValue(str(self.missionoptions.integration_time_step_size))
        self.txtintegrator_initial_step_size.SetValue(str(self.missionoptions.integrator_initial_step_size))
        self.txtintegrator_minimum_step_size.SetValue(str(self.missionoptions.integrator_minimum_step_size))
        self.txtintegrator_safety_factor.SetValue(str(self.missionoptions.integrator_safety_factor))
        self.txtintegrator_minimum_step_scale.SetValue(str(self.missionoptions.integrator_minimum_step_scale))
        self.txtintegrator_maximum_step_scale.SetValue(str(self.missionoptions.integrator_maximum_step_scale))
        self.txtintegrator_rejection_limit.SetValue(str(self.missionoptions.integrator_rejection_limit))
        self.cmbPeriapseBoundaryStateRepresentation.SetSelection(self.missionoptions.PeriapseBoundaryStateRepresentation)
        self.cmbParallelShootingStateRepresentation.SetSelection(self.missionoptions.ParallelShootingStateRepresentation)
        self.cmbParallelShootingConstraintStateRepresentation.SetSelection(self.missionoptions.ParallelShootingConstraintStateRepresentation)

        #if SplineEphem is active, show the SplineEphem controls
        if self.missionoptions.ephemeris_source == 2:
            self.lblSplineEphem_points_per_period.Show(True)
            self.txtSplineEphem_points_per_period.Show(True)
            self.lblSplineEphem_truncate_ephemeris_at_maximum_mission_epoch.Show(True)
            self.chkSplineEphem_truncate_ephemeris_at_maximum_mission_epoch.Show(True)
        else:            
            self.lblSplineEphem_points_per_period.Show(False)
            self.txtSplineEphem_points_per_period.Show(False)
            self.lblSplineEphem_truncate_ephemeris_at_maximum_mission_epoch.Show(False)
            self.chkSplineEphem_truncate_ephemeris_at_maximum_mission_epoch.Show(False)

        self.lblSPICE_high_fidelity_derivatives.Show(self.missionoptions.ephemeris_source == 1)
        self.chkSPICE_high_fidelity_derivatives.Show(self.missionoptions.ephemeris_source == 1)

        #if SRP is disabled, make the options associated with it invisible
        if self.missionoptions.perturb_SRP == 1:
            self.lblspacecraft_area.Show(True)
            self.lblcoefficient_of_reflectivity.Show(True)
            self.lblsolar_percentage.Show(True)
            self.lblsolar_flux.Show(True)
            self.lblspeed_of_light_vac.Show(True)
            self.txtspacecraft_area.Show(True)
            self.txtcoefficient_of_reflectivity.Show(True)
            self.txtsolar_percentage.Show(True)
            self.txtsolar_flux.Show(True)
            self.txtspeed_of_light_vac.Show(True)
        else:
            self.lblspacecraft_area.Show(False)
            self.lblcoefficient_of_reflectivity.Show(False)
            self.lblsolar_percentage.Show(False)
            self.lblsolar_flux.Show(False)
            self.lblspeed_of_light_vac.Show(False)
            self.txtspacecraft_area.Show(False)
            self.txtcoefficient_of_reflectivity.Show(False)
            self.txtsolar_percentage.Show(False)
            self.txtsolar_flux.Show(False)
            self.txtspeed_of_light_vac.Show(False)

        #only enable propagator switch if using a phase type that supports it
        if self.missionoptions.mission_type in [6, 7, 8, 9]:
            self.lblpropagatorType.Show(True)
            self.cmbpropagatorType.Show(True)
        else:
            self.lblpropagatorType.Show(False)
            self.cmbpropagatorType.Show(False)

        adaptive = self.missionoptions.integratorType == 0
        explicit_contract = adaptive and self.missionoptions.integrator_error_control_mode == 1
        legacy_controls = [self.lblintegrator_tolerance, self.txtintegrator_tolerance]
        explicit_controls = [self.lblintegrator_relative_tolerance, self.txtintegrator_relative_tolerance,
                             self.lblintegrator_absolute_tolerance_position, self.txtintegrator_absolute_tolerance_position,
                             self.lblintegrator_absolute_tolerance_velocity, self.txtintegrator_absolute_tolerance_velocity,
                             self.lblintegrator_absolute_tolerance_mass, self.txtintegrator_absolute_tolerance_mass,
                             self.lblintegrator_absolute_tolerance_time, self.txtintegrator_absolute_tolerance_time,
                             self.lblintegrator_absolute_tolerance_other, self.txtintegrator_absolute_tolerance_other,
                             self.lblintegrator_stm_relative_tolerance, self.txtintegrator_stm_relative_tolerance,
                             self.lblintegrator_stm_absolute_tolerance, self.txtintegrator_stm_absolute_tolerance]
        adaptive_controls = [self.lblintegrator_error_control_mode, self.cmbintegrator_error_control_mode,
                             self.lblintegrator_stm_error_control, self.cmbintegrator_stm_error_control,
                             self.lblintegrator_initial_step_size, self.txtintegrator_initial_step_size,
                             self.lblintegrator_minimum_step_size, self.txtintegrator_minimum_step_size,
                             self.lblintegrator_safety_factor, self.txtintegrator_safety_factor,
                             self.lblintegrator_minimum_step_scale, self.txtintegrator_minimum_step_scale,
                             self.lblintegrator_maximum_step_scale, self.txtintegrator_maximum_step_scale,
                             self.lblintegrator_rejection_limit, self.txtintegrator_rejection_limit]
        for control in legacy_controls:
            control.Show(adaptive and not explicit_contract)
        for control in explicit_controls:
            control.Show(explicit_contract)
        for control in adaptive_controls:
            control.Show(adaptive)

        self.lblintegratorType.Show(True)
        self.cmbintegratorType.Show(True)

        #re-size the panel
        self.Layout()
        if platform.system() == 'Windows':
            self.SetupScrolling(scrollToTop=False)

    def onTab(self, event):
       if event.GetKeyCode() == wx.WXK_TAB and not event.ShiftDown():
           event.EventObject.Navigate()
       if event.GetKeyCode() == wx.WXK_TAB and event.ShiftDown(): 
           event.EventObject.Navigate(flags=wx.NavigationKeyEvent.IsBackward)
       event.Skip()

    #event handlers for physics options
    def Changeephemeris_source(self, e):
        self.missionoptions.ephemeris_source = self.cmbephemeris_source.GetSelection()
        self.update()

    def ChangeSPICE_high_fidelity_derivatives(self, e):
        e.Skip()
        self.missionoptions.SPICE_high_fidelity_derivatives = int(self.chkSPICE_high_fidelity_derivatives.GetValue())

    def ChangeSPICE_leap_seconds_kernel(self, e):
        e.Skip()
        self.missionoptions.SPICE_leap_seconds_kernel = self.txtSPICE_leap_seconds_kernel.GetValue()

    def ChangeSPICE_reference_frame_kernel(self, e):
        e.Skip()
        self.missionoptions.SPICE_reference_frame_kernel = self.txtSPICE_reference_frame_kernel.GetValue()
        
    def ChangeSplineEphem_points_per_period(self, e):
        e.Skip()
        self.missionoptions.SplineEphem_points_per_period = int(self.txtSplineEphem_points_per_period.GetValue())

    def ChangeSplineEphem_non_central_body_sun_points_per_period(self, e):
        e.Skip()
        self.missionoptions.SplineEphem_non_central_body_sun_points_per_period = int(self.txtSplineEphem_non_central_body_sun_points_per_period.GetValue())

    def ChangeSplineEphem_truncate_ephemeris_at_maximum_mission_epoch(self, e):
        e.Skip()
        self.missionoptions.SplineEphem_truncate_ephemeris_at_maximum_mission_epoch = int(self.chkSplineEphem_truncate_ephemeris_at_maximum_mission_epoch.GetValue())
          
    def ChangeearliestPossibleEpoch(self, e):
        e.Skip()

        dateString = self.txtearliestPossibleEpoch.GetValue()

        from timeUtilities import stringToJD

        self.missionoptions.earliestPossibleEpoch = stringToJD(dateString, self.missionoptions.universe_folder)

        self.update()

    def ChangeearliestPossibleEpochCalendar(self, e):
        epoch = self.earliestPossibleEpochCalendar.GetDate()
        epoch = epoch.FromTimezone(wx.DateTime.TimeZone(offset=0))
        self.missionoptions.earliestPossibleEpoch = epoch.GetMJD()
        self.update()
        
    def ChangelatestPossibleEpoch(self, e):
        e.Skip()

        dateString = self.txtlatestPossibleEpoch.GetValue()

        from timeUtilities import stringToJD

        self.missionoptions.latestPossibleEpoch = stringToJD(dateString, self.missionoptions.universe_folder)

        self.update()

    def ChangelatestPossibleEpochCalendar(self, e):
        epoch = self.latestPossibleEpochCalendar.GetDate()
        epoch = epoch.FromTimezone(wx.DateTime.TimeZone(offset=0))
        self.missionoptions.latestPossibleEpoch = epoch.GetMJD()
        self.update()

    def Changeuniverse_folder(self, e):
        e.Skip()
        self.missionoptions.universe_folder = self.txtuniverse_folder.GetValue()

    def GetNewUniverseFolder(self, e):
        #file load dialog to get name of universe folder
        dlg = wx.DirDialog(self, "Choose a Universe folder", self.parent.Parent.dirname)
        if dlg.ShowModal() == wx.ID_OK:
            self.missionoptions.universe_folder = dlg.GetPath()
            self.txtuniverse_folder.SetValue(self.missionoptions.universe_folder)
        dlg.Destroy()

    def SetDefaultUniverse(self, e):
        self.missionoptions.universe_folder = self.parent.Parent.default_universe_path
        self.txtuniverse_folder.SetValue(self.missionoptions.universe_folder)

    def Changeperturb_SRP(self, e):
        self.missionoptions.perturb_SRP = int(self.chkperturb_SRP.GetValue())
        self.update()

    def Changeperturb_thirdbody(self, e):
        self.missionoptions.perturb_thirdbody = int(self.chkperturb_thirdbody.GetValue())
        self.parent.update()

    def Changeperturb_J2(self, e):
        self.missionoptions.perturb_J2 = int(self.chkperturb_J2.GetValue())
        self.parent.update()

    def Changespacecraft_area(self, e):
        e.Skip()
        self.missionoptions.spacecraft_area = eval(self.txtspacecraft_area.GetValue())

    def Changecoefficient_of_reflectivity(self, e):
        e.Skip()
        self.missionoptions.coefficient_of_reflectivity = eval(self.txtcoefficient_of_reflectivity.GetValue())

    def Changesolar_percentage(self, e):
        e.Skip()
        self.missionoptions.solar_percentage = eval(self.txtsolar_percentage.GetValue())

    def Changesolar_flux(self, e):
        e.Skip()
        self.missionoptions.solar_flux = eval(self.txtsolar_flux.GetValue())

    def Changespeed_of_light_vac(self, e):
        e.Skip()
        self.missionoptions.speed_of_light_vac = eval(self.txtspeed_of_light_vac.GetValue())

    def Changespiral_segments(self, e):
        e.Skip()
        self.missionoptions.spiral_segments = int(self.txtspiral_segments.GetValue())

    def ChangeIntegratorTolerance(self, e):
        e.Skip()
        previous = self.missionoptions.integrator_tolerance
        try:
            self.missionoptions.integrator_tolerance = float(self.txtintegrator_tolerance.GetValue())
            validate_adaptive_options(self.missionoptions)
        except (TypeError, ValueError) as error:
            self.missionoptions.integrator_tolerance = previous
            wx.MessageBox(str(error), "Invalid adaptive integration option", wx.OK | wx.ICON_ERROR)
            self.update()

    def ChangepropagatorType(self, e):
        self.missionoptions.propagatorType = self.cmbpropagatorType.GetSelection()
        self.update()
        e.Skip()

    def ChangeintegratorType(self, e):
        self.missionoptions.integratorType = selection_to_integrator_type(self.cmbintegratorType.GetSelection())
        self.parent.update()
        e.Skip()

    def Changeintegrator_error_control_mode(self, e):
        self.missionoptions.integrator_error_control_mode = self.cmbintegrator_error_control_mode.GetSelection()
        self.update()
        e.Skip()

    def Changeintegrator_stm_error_control(self, e):
        self.missionoptions.integrator_stm_error_control = self.cmbintegrator_stm_error_control.GetSelection()
        self.update()
        e.Skip()

    def ChangeAdaptiveIntegratorSetting(self, e):
        e.Skip()
        control_to_attribute = {
            self.txtintegrator_relative_tolerance: 'integrator_relative_tolerance',
            self.txtintegrator_absolute_tolerance_position: 'integrator_absolute_tolerance_position',
            self.txtintegrator_absolute_tolerance_velocity: 'integrator_absolute_tolerance_velocity',
            self.txtintegrator_absolute_tolerance_mass: 'integrator_absolute_tolerance_mass',
            self.txtintegrator_absolute_tolerance_time: 'integrator_absolute_tolerance_time',
            self.txtintegrator_absolute_tolerance_other: 'integrator_absolute_tolerance_other',
            self.txtintegrator_stm_relative_tolerance: 'integrator_stm_relative_tolerance',
            self.txtintegrator_stm_absolute_tolerance: 'integrator_stm_absolute_tolerance',
            self.txtintegrator_initial_step_size: 'integrator_initial_step_size',
            self.txtintegrator_minimum_step_size: 'integrator_minimum_step_size',
            self.txtintegrator_safety_factor: 'integrator_safety_factor',
            self.txtintegrator_minimum_step_scale: 'integrator_minimum_step_scale',
            self.txtintegrator_maximum_step_scale: 'integrator_maximum_step_scale',
            self.txtintegrator_rejection_limit: 'integrator_rejection_limit',
        }
        control = e.GetEventObject()
        attribute = control_to_attribute[control]
        previous = getattr(self.missionoptions, attribute)
        try:
            value = int(control.GetValue()) if attribute == 'integrator_rejection_limit' else float(control.GetValue())
            setattr(self.missionoptions, attribute, value)
            validate_adaptive_options(self.missionoptions)
        except (TypeError, ValueError) as error:
            setattr(self.missionoptions, attribute, previous)
            wx.MessageBox(str(error), "Invalid adaptive integration option", wx.OK | wx.ICON_ERROR)
            self.update()

    def Changeintegration_time_step_size(self, e):
        previous = self.missionoptions.integration_time_step_size
        try:
            self.missionoptions.integration_time_step_size = float(self.txtintegration_time_step_size.GetValue())
            if self.missionoptions.integration_time_step_size <= 0.0:
                raise ValueError("integration_time_step_size must be strictly positive.")
        except (TypeError, ValueError) as error:
            self.missionoptions.integration_time_step_size = previous
            wx.MessageBox(str(error), "Invalid integration option", wx.OK | wx.ICON_ERROR)
            self.update()
        self.parent.update()
        e.Skip()
        
    def ChangePeriapseBoundaryStateRepresentation(self, e):
        self.missionoptions.PeriapseBoundaryStateRepresentation = self.cmbPeriapseBoundaryStateRepresentation.GetSelection()
        self.parent.update()
        self.missionoptions.DisassembleMasterDecisionVector()
        self.missionoptions.ConvertDecisionVector()
        self.missionoptions.AssembleMasterDecisionVector()
        e.Skip()

    def ChangeParallelShootingStateRepresentation(self, e):
        self.missionoptions.ParallelShootingStateRepresentation = self.cmbParallelShootingStateRepresentation.GetSelection()
        self.parent.update()
        self.missionoptions.DisassembleMasterDecisionVector()
        self.missionoptions.ConvertDecisionVector()
        self.missionoptions.AssembleMasterDecisionVector()
        e.Skip()

    def ChangeParallelShootingConstraintStateRepresentation(self, e):
        self.missionoptions.ParallelShootingConstraintStateRepresentation = self.cmbParallelShootingConstraintStateRepresentation.GetSelection()
        self.parent.update()
        e.Skip()
