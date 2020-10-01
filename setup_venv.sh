#!/bin/bash

set -e

venv_path=".venv"

pip install --user virtualenv

virtualenv $venv_path

. $venv_path/bin/activate
pip install -U pip setuptools
pip install -r requirements.txt

echo
echo "Isilon Data Insights Connector virtual environment setup at $venv_path."
echo "To activate the virtual environment run: . $venv_path/bin/activate"
