#include <iostream>
#include "SphericalPositionConstraint.h"

int main()
{
    using namespace EMTG;
    // Both spherical encodings use a direct radius only for the central body.
    struct Case { StateRepresentation state; bool directRadius; };
    const Case cases[] = {
        {Cartesian, false}, {SphericalRADEC, true}, {SphericalAZFPA, true},
        {COE, false}, {MEE, false}, {IncomingBplane, false},
        {OutgoingBplane, false}, {IncomingBplaneRpTA, false},
        {OutgoingBplaneRpTA, false}
    };
    for (const auto& item : cases)
    {
        if (usesSphericalPositionConstraint(false, item.state))
            return 1;
        if (usesSphericalPositionConstraint(true, item.state) != item.directRadius)
            return 2;
    }
    std::cout << "Spherical position constraint representations passed\n";
    return 0;
}
