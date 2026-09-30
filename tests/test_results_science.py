import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import math
from PyEMTG.Results import expended_delta_v_km_s

def test_expended_delta_v_uses_exact_propellant_and_local_isp():
    metrics = {
        "thruster_duty_cycle": 0.9,
        "electric_propellant_used": 10.0,
        "deterministic_delta_v": 0.05,
    }
    events = [
        {
            "timestep_days": 2.0,
            "throttle_fraction": 0.5,
            "mass_flow_rate_kg_s": 0.0001,
            "isp_s": 2000.0,
            "mass": 1000.0,
        }
    ]
    expected = 0.05 + 2000.0 * 9.80665 / 1000.0 * math.log(1000.0 / 990.0)
    assert expended_delta_v_km_s(metrics, events) == expected


