#!/bin/bash

# Vérifier si un argument a été fourni
if [ -z "$1" ]; then
    echo "Aucun argument fourni. Aucun script ne sera ajouté à 1hcron.sh."
else
    # Récupérer l'argument
    ARGUMENT="$1"

    # Initialiser la variable pour le script Python
    PYTHON_SCRIPT=""

    # Déterminer le script Python en fonction de l'argument
    if [ "$ARGUMENT" == "main" ]; then
        PYTHON_SCRIPT="python3 Bot_MEXC/strategies/envelopes/main.py"
    else
        echo "Argument non reconnu. Aucun ajout ne sera effectué."
    fi

    # Si un script Python a été défini, procéder à l'ajout
    if [ -n "$PYTHON_SCRIPT" ]; then
        # Vérifier si la ligne existe déjà dans 1hcron.sh
        if grep -Fxq "$PYTHON_SCRIPT" Bot_MEXC/1hcron.sh; then
            echo "Le script $PYTHON_SCRIPT existe déjà dans 1hcron.sh"
        else
            # Ajouter la ligne au fichier 1hcron.sh
            echo "$PYTHON_SCRIPT" >> Bot_MEXC/1hcron.sh
            echo "Le script $PYTHON_SCRIPT a été ajouté à 1hcron.sh"
        fi
    fi
fi

echo "Mise à jour du serveur..."
sudo apt-get update

echo "Installation de pip..."
sudo apt install pip -y

# Créer le fichier de log s'il n'existe pas
touch cronlog.log

echo "Installation des packages nécessaires..."
cd Bot_MEXC
sudo apt-get install python3-venv -y
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
git update-index --assume-unchanged secret.py
cd ..

# Ajouter la tâche cron si elle n'existe pas déjà
crontab -l | grep -q 'bash ./Bot_MEXC/1hcron.sh'
if [ $? -ne 0 ]; then
    # Ajouter la tâche cron
    (crontab -l 2>/dev/null; echo "0 * * * * /bin/bash ./Bot_MEXC/1hcron.sh >> cronlog.log") | crontab -
    echo "Tâche cron ajoutée avec succès."
else
    echo "La tâche cron existe déjà."
fi
