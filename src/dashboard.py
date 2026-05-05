from flask import Flask
from flask_cors import CORS
import requests
import json
import threading
import time

app = Flask(__name__)
app.config['JSON_SORT_KEYS'] = False
CORS(app, resources={r"/get": {"origins": "http://localhost:5173"}})

reais = 0.0
watts = 0.0
wattsAcumulativo = 0.0
reaisAcumulativo = 0.0

def atualizar_dados():
    global wattsAcumulativo
    global reaisAcumulativo
    global watts
    global reais

    while True:
        url = "http://46.17.108.113:1026/v2/entities/urn:ngsi-ld:Meterwatt:001"
        payload = {}
        headers = {
            'Accept': 'application/json',
            'fiware-service': 'smart',
            'fiware-servicepath': '/',
            "Access-Control-Allow-Origin": "http://localhost:5173"
        }

        response = requests.request("GET", url, headers=headers, data=payload)
        data = json.loads(response.text)

        if 'pot' in data and 'value' in data['pot']:
            watts = data['pot']['value']
            transform = watts * 0.0012
            reais = transform / 1000
            reaisAcumulativo += reais
            wattsAcumulativo += watts

        time.sleep(10)

atualizacao_thread = threading.Thread(target=atualizar_dados)
atualizacao_thread.daemon = True
atualizacao_thread.start()

@app.route('/get', methods=['GET'])
def leitura():
    global reais
    global watts
    global reaisAcumulativo
    global wattsAcumulativo

    return json.dumps({
        'reais': reais,
        'watts': watts,
        'reais_acumulativo': reaisAcumulativo,
        'watts_acumulativo': wattsAcumulativo
    })

if __name__ == "__main__":
    app.run(host='0.0.0.0', debug=True, port='5000')
