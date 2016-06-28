#!/bin/bash

set -e

venv_path=".venv"

pip install virtualenv

virtualenv $venv_path

. $venv_path/bin/activate
pip install -r requirements.txt
redir_url=`curl -I https://github.com/Isilon/isilon_sdk/releases/latest | grep ^Location: | awk '{print $2;}' | sed 's/tag/download/' | sed 's/\\r//'`
redir_dirname=`dirname $redir_url`
version_num=`basename $redir_url`
zip_filename="isi_sdk.python."${version_num}".zip"
redir_file="/$version_num/$zip_filename"
if [ -e $zip_filename ]
then
    rm $zip_filename
fi
wget "$redir_dirname/$redir_file"
unzip -f $zip_filename
cd isi_sdk/isi_sdk_8_0
python setup.py install
cd ../isi_sdk_7_2
python setup.py install

echo
echo "Isilon Data Insights Connector virtual environment setup at $venv_path."
echo "To activate the virtual environment run: . $venv_path/bin/activate"
