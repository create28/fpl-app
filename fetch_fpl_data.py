import requests
import csv
import datetime
import os
import pandas as pd
import urllib3
from http.server import HTTPServer, BaseHTTPRequestHandler
import json
from urllib.parse import parse_qs, urlparse
import sqlite3
import time
from datetime import datetime, timedelta
import sys
import threading

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Initialize SQLite database for historical data
def init_db():
    """Initialize the SQLite database for storing historical FPL data."""
    print("Starting database initialization...")
    try:
        conn = sqlite3.connect('fpl_history.db')
        c = conn.cursor()
        
        # Create main data table
        c.execute('''CREATE TABLE IF NOT EXISTS fpl_data
                     (gameweek INTEGER,
                      team_id INTEGER,
                      team_name TEXT,
                      manager_name TEXT,
                      gw_points INTEGER,
                      total_points INTEGER,
                      rank INTEGER,
                      team_value REAL,
                      bank_balance REAL,
                      timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                      PRIMARY KEY (gameweek, team_id))''')
        print("Created fpl_data table")
        
        # Create award winners table
        c.execute('''CREATE TABLE IF NOT EXISTS award_winners
                     (gameweek INTEGER,
                      award_type TEXT,
                      team_id INTEGER,
                      team_name TEXT,
                      manager_name TEXT,
                      points INTEGER,
                      PRIMARY KEY (gameweek, award_type))''')
        print("Created award_winners table")
        
        conn.commit()
        conn.close()
        print("Database initialization complete")
    except Exception as e:
        print(f"Error initializing database: {e}")
        raise

def store_fpl_data(gameweek, data):
    """Store FPL data in the database."""
    conn = sqlite3.connect('fpl_history.db')
    c = conn.cursor()
    
    for team in data:
        c.execute('''INSERT OR REPLACE INTO fpl_data 
                    (gameweek, team_id, team_name, manager_name, gw_points, 
                     total_points, rank, team_value, bank_balance)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                 (gameweek, team['team_id'], team['team_name'], 
                  team['manager_name'], team['gw_points'], team['total_points'],
                  team['rank'], team['team_value'], team['bank_balance']))
    
    conn.commit()
    conn.close()

def store_award_winners(gameweek, data, gameweek_champions):
    """Store award winners in the database."""
    conn = sqlite3.connect('fpl_history.db')
    c = conn.cursor()
    
    # Calculate all awards
    awards = calculate_awards(data)
    
    # Store weekly champions
    for champion in awards['weekly_champion']:
        c.execute('''INSERT OR REPLACE INTO award_winners 
                    (gameweek, award_type, team_id, team_name, manager_name, points)
                    VALUES (?, ?, ?, ?, ?, ?)''',
                 (gameweek, 'weekly_champion', 
                  next((team['team_id'] for team in data 
                       if team['team_name'] == champion['team_name']), None),
                  champion['team_name'], champion['manager_name'], champion['points']))
    
    # Store wooden spoons
    for spoon in awards['wooden_spoon']:
        c.execute('''INSERT OR REPLACE INTO award_winners 
                    (gameweek, award_type, team_id, team_name, manager_name, points)
                    VALUES (?, ?, ?, ?, ?, ?)''',
                 (gameweek, 'wooden_spoon',
                  next((team['team_id'] for team in data 
                       if team['team_name'] == spoon['team_name']), None),
                  spoon['team_name'], spoon['manager_name'], spoon['points']))
    
    # Store gameweek champions
    for champion in gameweek_champions:
        c.execute('''INSERT OR REPLACE INTO award_winners 
                    (gameweek, award_type, team_id, team_name, manager_name, points)
                    VALUES (?, ?, ?, ?, ?, ?)''',
                 (gameweek, 'gameweek_champion',
                  next((team['team_id'] for team in data 
                       if team['team_name'] == champion['team_name']), None),
                  champion['team_name'], champion['manager_name'], champion['points']))
    
    conn.commit()
    conn.close()

def get_award_winners(gameweek, award_type=None):
    """Retrieve award winners for a specific gameweek and optionally filter by award type."""
    conn = sqlite3.connect('fpl_history.db')
    c = conn.cursor()
    
    if award_type:
        c.execute('''SELECT * FROM award_winners WHERE gameweek = ? AND award_type = ?''', 
                 (gameweek, award_type))
    else:
        c.execute('''SELECT * FROM award_winners WHERE gameweek = ?''', (gameweek,))
    
    winners = []
    for row in c.fetchall():
        winners.append({
            'team_name': row[3],
            'manager_name': row[4],
            'points': row[5]
        })
    
    conn.close()
    return winners

def get_historical_data(gameweek):
    """Retrieve historical FPL data from the database."""
    conn = sqlite3.connect('fpl_history.db')
    c = conn.cursor()
    
    # Get current gameweek data
    c.execute('''SELECT * FROM fpl_data WHERE gameweek = ?''', (gameweek,))
    current_data = []
    for row in c.fetchall():
        current_data.append({
            'team_id': row[1],
            'team_name': row[2],
            'manager_name': row[3],
            'gw_points': row[4],
            'total_points': row[5],
            'rank': row[6],
            'team_value': row[7],
            'bank_balance': row[8]
        })
    
    # Get previous gameweek data for comparison
    if gameweek > 1:
        c.execute('''SELECT * FROM fpl_data WHERE gameweek = ?''', (gameweek - 1,))
        previous_data = {row[1]: row[6] for row in c.fetchall()}  # team_id: rank
        
        # Add rank changes
        for team in current_data:
            if team['team_id'] in previous_data:
                team['rank_change'] = previous_data[team['team_id']] - team['rank']
    
    conn.close()
    return current_data

# Function to fetch data from a given URL
def fetch_data(url):
    try:
        response = requests.get(url, verify=False)  # Disable SSL verification
        if response.status_code == 200:
            return response.json()
        else:
            print(f"Error fetching data: {response.status_code}")
            return None
    except requests.exceptions.SSLError as e:
        print(f"SSL error: {e}")
        return None

def get_all_gameweek_data():
    """Fetch data for all available gameweeks."""
    all_data = {}
    for gameweek in range(1, 39):  # 1-38
        data = get_fpl_data(gameweek)
        if data:
            all_data[gameweek] = data
    return all_data

def calculate_awards(data):
    """Calculate all awards for a given gameweek's data."""
    if not data:
        return {
            'weekly_champion': [],
            'wooden_spoon': [],
            'gameweek_champion': []
        }

    # Filter out teams with 0 points
    valid_teams = [team for team in data if team['gw_points'] > 0]
    
    if not valid_teams:
        return {
            'weekly_champion': [],
            'wooden_spoon': [],
            'gameweek_champion': []
        }

    # Find weekly champion (most points)
    max_points = max(team['gw_points'] for team in valid_teams)
    weekly_champions = [team for team in valid_teams if team['gw_points'] == max_points]
    
    # Find wooden spoon (least points)
    min_points = min(team['gw_points'] for team in valid_teams)
    wooden_spoons = [team for team in valid_teams if team['gw_points'] == min_points]
    
    return {
        'weekly_champion': [{
            'team_name': champ['team_name'],
            'manager_name': champ['manager_name'],
            'points': champ['gw_points']
        } for champ in weekly_champions],
        'wooden_spoon': [{
            'team_name': spoon['team_name'],
            'manager_name': spoon['manager_name'],
            'points': spoon['gw_points']
        } for spoon in wooden_spoons]
    }

def calculate_gameweek_champion(gameweek, current_data, previous_data):
    """Calculate the gameweek champion based on points improvement."""
    if not previous_data:
        return []
    
    # Filter out teams with 0 points in current gameweek
    valid_current_teams = [team for team in current_data if team['gw_points'] > 0]
    if not valid_current_teams:
        return []
    
    improvements = []
    for current_team in valid_current_teams:
        for previous_team in previous_data:
            if current_team['team_id'] == previous_team['team_id']:
                improvement = current_team['gw_points'] - previous_team['gw_points']
                improvements.append((current_team, improvement))
                break

    if not improvements:
        return []

    # Find the highest positive improvement, if any
    positive_improvements = [imp[1] for imp in improvements if imp[1] > 0]
    if positive_improvements:
        max_improvement = max(positive_improvements)
        champions = [imp for imp in improvements if imp[1] == max_improvement]
    else:
        # If no positive, use the highest (least negative or zero) improvement
        max_improvement = max(imp[1] for imp in improvements)
        champions = [imp for imp in improvements if imp[1] == max_improvement]

    return [{
        'team_name': champ[0]['team_name'],
        'manager_name': champ[0]['manager_name'],
        'points': champ[1]  # This is the difference
    } for champ in champions]

def save_data_to_json(data, filename):
    """Save data to a JSON file."""
    with open(filename, 'w') as f:
        json.dump(data, f)

def load_data_from_json(filename):
    """Load data from a JSON file if it exists and is not too old."""
    if not os.path.exists(filename):
        return None
    
    # Check if file is older than 1 hour
    file_time = datetime.fromtimestamp(os.path.getmtime(filename))
    if datetime.now() - file_time > timedelta(hours=1):
        return None
    
    try:
        with open(filename, 'r') as f:
            return json.load(f)
    except:
        return None

def get_fpl_data(gameweek):
    """Fetch and process FPL data for a specific gameweek."""
    # First try to get historical data from database
    historical_data = get_historical_data(gameweek)
    if historical_data and any(team['gw_points'] > 0 for team in historical_data):
        # Get previous gameweek data for gameweek champion calculation
        previous_data = get_historical_data(gameweek - 1) if gameweek > 1 else None
        
        # Calculate all awards
        awards = calculate_awards(historical_data)
        awards['gameweek_champion'] = calculate_gameweek_champion(gameweek, historical_data, previous_data)
        
        return {
            'standings': historical_data,
            'awards': awards
        }

    # If no historical data or all points are 0, try to get from JSON cache
    cache_file = f'cache/gameweek_{gameweek}.json'
    cached_data = load_data_from_json(cache_file)
    if cached_data and any(team['gw_points'] > 0 for team in cached_data.get('standings', [])):
        return cached_data

    # If no cached data or all points are 0, fetch from API
    league_id = 1658794
    league_url = f"https://fantasy.premierleague.com/api/leagues-classic/{league_id}/standings/"
    league_data = fetch_data(league_url)

    if not league_data:
        return None

    standings = league_data['standings']['results']
    current_data = []
    
    for team in standings:
        team_id = team['entry']
        team_name = team['entry_name']
        manager_name = team['player_name']
        rank = team['rank']

        # Fetch gameweek points and other info
        gw_url = f"https://fantasy.premierleague.com/api/entry/{team_id}/event/{gameweek}/picks/"
        gw_data = fetch_data(gw_url)

        if gw_data and 'entry_history' in gw_data:
            gw_points = gw_data['entry_history']['points']
            total_points = gw_data['entry_history']['total_points']
            team_value = gw_data['entry_history']['value'] / 10
            bank_balance = gw_data['entry_history']['bank'] / 10
        else:
            # Try to get data from the current gameweek endpoint
            current_url = f"https://fantasy.premierleague.com/api/entry/{team_id}/"
            current_data = fetch_data(current_url)
            if current_data and 'current_event' in current_data:
                gw_points = current_data['current_event']['points']
                total_points = current_data['current_event']['total_points']
                team_value = current_data['current_event']['value'] / 10
                bank_balance = current_data['current_event']['bank'] / 10
            else:
                gw_points = 0
                total_points = 0
                team_value = 0
                bank_balance = 0

        team_data = {
            'team_id': team_id,
            'team_name': team_name,
            'manager_name': manager_name,
            'gw_points': gw_points,
            'total_points': total_points,
            'rank': rank,
            'team_value': team_value,
            'bank_balance': bank_balance
        }
        current_data.append(team_data)

    # Only store data if we have valid points
    if any(team['gw_points'] > 0 for team in current_data):
        store_fpl_data(gameweek, current_data)
        
        # Get previous gameweek data for comparison
        previous_data = get_historical_data(gameweek - 1) if gameweek > 1 else None
        if previous_data:
            previous_ranks = {team['team_id']: team['rank'] for team in previous_data}
            for team in current_data:
                if team['team_id'] in previous_ranks:
                    team['rank_change'] = previous_ranks[team['team_id']] - team['rank']

        # Calculate all awards
        awards = calculate_awards(current_data)
        awards['gameweek_champion'] = calculate_gameweek_champion(gameweek, current_data, previous_data)
        
        # Store award winners
        store_award_winners(gameweek, current_data, awards['gameweek_champion'])

        result_data = {
            'standings': current_data,
            'awards': awards
        }

        # Cache the result
        os.makedirs('cache', exist_ok=True)
        save_data_to_json(result_data, cache_file)

        return result_data
    
    return None

def get_latest_valid_gameweek():
    """Find the latest gameweek that has valid data (not all zeros)."""
    try:
        # Start from gameweek 38 and work backwards
        for gameweek in range(38, 0, -1):
            print(f"Checking gameweek {gameweek}")
            data = get_fpl_data(gameweek)
            
            # Check if we have valid data with points
            if data and data.get('standings'):
                # Check if any team has points in this gameweek
                if any(team.get('gw_points', 0) > 0 for team in data['standings']):
                    print(f"Found valid data in gameweek {gameweek}")
                    return gameweek
                else:
                    print(f"Gameweek {gameweek} has no points data")
            else:
                print(f"No data found for gameweek {gameweek}")
        
        print("No valid data found in any gameweek")
        return 1  # Default to gameweek 1 if no valid data found
    except Exception as e:
        print(f"Error in get_latest_valid_gameweek: {e}")
        return 1  # Default to gameweek 1 on error

def fetch_current_gameweek():
    """Get the latest gameweek with valid data."""
    return get_latest_valid_gameweek()

def preload_data():
    """Preload data for latest valid gameweek."""
    print("Preloading FPL data...")
    latest_gw = get_latest_valid_gameweek()
    print(f"Latest valid gameweek: {latest_gw}")
    
    try:
        data = get_fpl_data(latest_gw)
        if data:
            print(f"Successfully loaded gameweek {latest_gw}")
        else:
            print(f"Failed to load gameweek {latest_gw}")
    except Exception as e:
        print(f"Error loading gameweek {latest_gw}: {e}")
    
    print("Data preloading complete!")

def is_game_active():
    """Check if there's an active FPL gameweek."""
    try:
        response = requests.get('https://fantasy.premierleague.com/api/bootstrap-static/', verify=False)
        if response.status_code == 200:
            data = response.json()
            events = data['events']
            for event in events:
                if event['is_current']:
                    # Check if the gameweek is active (has started but not finished)
                    return event['is_current'] and not event['finished']
        return False
    except Exception as e:
        print(f"Error checking game status: {e}")
        return False

def refresh_data_periodically():
    """Periodically refresh FPL data."""
    while True:
        try:
            # Get the latest valid gameweek
            current_gw = get_latest_valid_gameweek()
            if current_gw is None:
                print("Could not determine current gameweek, retrying in 5 minutes...")
                time.sleep(300)
                continue

            # Fetch and store data for the current gameweek
            data = get_fpl_data(current_gw)
            if data:
                store_fpl_data(current_gw, data)
                
                # Get previous gameweek data for comparison
                if current_gw > 1:
                    prev_data = get_fpl_data(current_gw - 1)
                    if prev_data:
                        gameweek_champions = calculate_gameweek_champion(current_gw, data, prev_data)
                        store_award_winners(current_gw, data, gameweek_champions)
                
                print(f"Successfully updated data for gameweek {current_gw}")
            else:
                print(f"Failed to fetch data for gameweek {current_gw}")

            # Wait for 5 minutes before next refresh
            time.sleep(300)
            
        except Exception as e:
            print(f"Error in periodic refresh: {e}")
            # Wait for 1 minute before retrying after an error
            time.sleep(60)

def force_refresh_all_gameweeks():
    print('Forcing refresh for all gameweeks...')
    for gw in range(1, 39):
        print(f'Forcing refresh for gameweek {gw}')
        get_fpl_data(gw)
    print('Full refresh complete!')

def get_available_gameweeks():
    """Get list of available gameweeks from the database."""
    print("Fetching available gameweeks...")
    try:
        conn = sqlite3.connect('fpl_history.db')
        c = conn.cursor()
        c.execute('SELECT DISTINCT gameweek FROM fpl_data ORDER BY gameweek')
        gameweeks = [row[0] for row in c.fetchall()]
        conn.close()
        print(f"Found gameweeks: {gameweeks}")
        return gameweeks
    except Exception as e:
        print(f"Error fetching available gameweeks: {e}")
        return []

def read_gameweek_data(gameweek):
    """Read and display data for a specific gameweek."""
    conn = sqlite3.connect('fpl_history.db')
    c = conn.cursor()
    
    print(f"\nGameweek {gameweek} Data:")
    print("-" * 50)
    
    # Get standings data
    c.execute('''SELECT team_name, manager_name, gw_points, total_points, rank 
                 FROM fpl_data WHERE gameweek = ? ORDER BY rank''', (gameweek,))
    standings = c.fetchall()
    
    print("Standings:")
    for team in standings:
        print(f"Rank {team[3]}: {team[0]} ({team[1]}) - {team[2]} points")
    
    # Get award winners
    c.execute('''SELECT award_type, team_name, manager_name, points 
                 FROM award_winners WHERE gameweek = ?''', (gameweek,))
    awards = c.fetchall()
    
    print("\nAwards:")
    for award in awards:
        print(f"{award[0]}: {award[1]} ({award[2]}) - {award[3]} points")
    
    conn.close()

class FPLHandler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        """Handle HEAD requests."""
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()

    def do_GET(self):
        parsed_path = urlparse(self.path)
        path = parsed_path.path
        
        print(f"Received request for path: {path}")
        
        if path == '/':
            # Serve the main HTML page
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            with open('index.html', 'rb') as f:
                self.wfile.write(f.read())
            print("Served index.html")
        
        elif path == '/styles.css':
            # Serve CSS file
            self.send_response(200)
            self.send_header('Content-type', 'text/css')
            self.end_headers()
            with open('styles.css', 'rb') as f:
                self.wfile.write(f.read())
        
        elif path == '/script.js':
            # Serve JavaScript file
            self.send_response(200)
            self.send_header('Content-type', 'application/javascript')
            self.end_headers()
            with open('script.js', 'rb') as f:
                self.wfile.write(f.read())
        
        elif path == '/api/gameweeks':
            # Return list of available gameweeks with data
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            gameweeks = get_available_gameweeks()
            print(f"Sending gameweeks: {gameweeks}")
            self.wfile.write(json.dumps(gameweeks).encode())
        
        elif path == '/api/current-gameweek':
            # Return the latest gameweek with data
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            gameweeks = get_available_gameweeks()
            current_gw = gameweeks[-1] if gameweeks else 1
            self.wfile.write(json.dumps({'current_gameweek': current_gw}).encode())
        
        elif path == '/api/all-data':
            # Return data for all gameweeks
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            all_data = get_all_gameweek_data()
            self.wfile.write(json.dumps(all_data).encode())
        
        elif path.startswith('/api/data/'):
            # Handle data requests
            try:
                gameweek = int(parsed_path.path.split('/')[-1])
                print(f"Fetching data for gameweek {gameweek}")
                if 1 <= gameweek <= 38:
                    data = get_fpl_data(gameweek)
                    if data:
                        self.send_response(200)
                        self.send_header('Content-type', 'application/json')
                        self.end_headers()
                        self.wfile.write(json.dumps(data).encode())
                        print(f"Successfully sent data for gameweek {gameweek}")
                    else:
                        print(f"No data found for gameweek {gameweek}")
                        self.send_error(500, "Failed to fetch FPL data")
                else:
                    print(f"Invalid gameweek number: {gameweek}")
                    self.send_error(400, "Invalid gameweek number")
            except ValueError as e:
                print(f"Error parsing gameweek: {e}")
                self.send_error(400, "Invalid gameweek format")
            except Exception as e:
                print(f"Error handling data request: {e}")
                self.send_error(500, "Internal server error")
        
        else:
            self.send_error(404, "Not found")

def run_server():
    try:
        # Initialize database
        print("Initializing database...")
        init_db()
        
        # Create cache directory if it doesn't exist
        os.makedirs('cache', exist_ok=True)
        print("Cache directory created/verified")
        
        # Preload initial data
        print("Preloading initial data...")
        latest_gw = get_latest_valid_gameweek()
        print(f"Latest valid gameweek: {latest_gw}")
        
        # Force fetch the latest gameweek data
        data = get_fpl_data(latest_gw)
        if data:
            print(f"Successfully loaded gameweek {latest_gw}")
            store_fpl_data(latest_gw, data['standings'])
            store_award_winners(latest_gw, data['standings'], data['awards'].get('gameweek_champion', []))
        else:
            print(f"Failed to load gameweek {latest_gw}")
        
        # Start the periodic refresh thread
        print("Starting periodic refresh thread...")
        refresh_thread = threading.Thread(target=refresh_data_periodically, daemon=True)
        refresh_thread.start()
        
        # Start the server
        server_address = ('', int(os.environ.get('PORT', 8000)))
        print(f"Starting server on port {server_address[1]}")
        httpd = HTTPServer(server_address, FPLHandler)
        print("Server started successfully")
        httpd.serve_forever()
    except Exception as e:
        print(f"Error starting server: {e}")
        raise

def main():
    """Main function to initialize and run the server with periodic updates."""
    try:
        # Initialize the database
        init_db()
        
        # Start the periodic refresh in a separate thread
        refresh_thread = threading.Thread(target=refresh_data_periodically, daemon=True)
        refresh_thread.start()
        
        # Run the server
        run_server()
    except Exception as e:
        print(f"Fatal error: {e}")
        sys.exit(1)

def get_current_gameweek_data():
    """Fetch current gameweek data from the bootstrap-static endpoint."""
    try:
        response = requests.get('https://fantasy.premierleague.com/api/bootstrap-static/', verify=False)
        if response.status_code == 200:
            data = response.json()
            events = data['events']
            current_event = next((event for event in events if event['is_current']), None)
            
            if current_event:
                print(f"\nCurrent Gameweek Information:")
                print("-" * 50)
                print(f"Gameweek: {current_event['id']}")
                print(f"Name: {current_event['name']}")
                print(f"Deadline: {current_event['deadline_time']}")
                print(f"Finished: {current_event['finished']}")
                print(f"Data Checked: {current_event['data_checked']}")
                return current_event
            else:
                print("No current gameweek found")
                return None
        else:
            print(f"Error fetching current gameweek data: {response.status_code}")
            return None
    except Exception as e:
        print(f"Error in get_current_gameweek_data: {e}")
        return None

if __name__ == "__main__":
    main()
