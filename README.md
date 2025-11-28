# ai
just a simple machine learning project

## Ollama
https://ollama.com/  

## Setup
curl -fsSL https://ollama.com/install.sh | sh

## Step by step
1. Setup a github repo
2. Clone repo
3. in terminal go to the folder odf the project
4. run python3 -m venv venv to create the virtual enviroment
5. activate it source venv/bin/activate
6. installera bibliotek pip install python-dotenv
7. nano .env and paste the config
8. pip install yfinance
9. pip install ollama
10. pip install schedule
11. pip install python-dateutil
12. pip install pandas
13. pip install psutil
14. pip install psutil wmi


# Kör agenten med nohup och skicka output till en loggfil
nohup venv/bin/python agent.py > agent.log 2>&1 &

## Python3 setup
python3 -m venv venv

source venv/bin/activate
