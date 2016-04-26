#!/bin/bash

set -e

pip install virtualenv

if [ -f ".venv" ]
then
    echo "venv found, no need to create it"
else
    virtualenv .venv
fi

. .venv/bin/activate
pip install -r requirements.txt
