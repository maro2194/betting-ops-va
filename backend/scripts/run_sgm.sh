#!/bin/bash
cd /home/jsb
exec timeout 240 python3 entain_sgm_add_legs.py neds williamdean327 'Deanslister27!' > /tmp/sgm_run2.log 2>&1
