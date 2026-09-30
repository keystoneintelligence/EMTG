#pragma once

#include "EMTG_enums.h"

namespace EMTG
{
    // A central-body distance is the encoded radius for either spherical state.
    constexpr bool usesSphericalPositionConstraint(
        bool relativeToCentralBody, StateRepresentation state)
    {
        return relativeToCentralBody
            && (state == StateRepresentation::SphericalRADEC
                || state == StateRepresentation::SphericalAZFPA);
    }
}
