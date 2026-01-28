import sqlite3
import requests
from datetime import datetime


class DatabaseManager:
    
    def __init__(self, db_path):
        """
        Initialize the DatabaseManager with a specific database path.
        
        Args:
            db_path (str): Path to the SQLite database file
        """
        self.db_path = db_path
        self.initialize_database()
    
    def _get_connection(self):
        """
        Create and return a database connection.
        
        Returns:
            sqlite3.Connection: Database connection object
        """
        return sqlite3.connect(self.db_path)
    
    def initialize_database(self):
        """
        Initialize the database and create necessary tables.
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # Create table for events from API
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS events (
                event_id INTEGER PRIMARY KEY,
                league TEXT,
                event_name TEXT NOT NULL,
                date TEXT,
                start_time TEXT,
                end_time TEXT,
                home_team TEXT,
                away_team TEXT,
                home_icon TEXT,
                away_icon TEXT,
                stream_url TEXT
            )
        ''')
        
        conn.commit()
        conn.close()
    
    # ---Clear database methods---
    def clear_database(self):
        """
        Clear all events from the database.
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute('DELETE FROM events')
        
        conn.commit()
        conn.close()
    
    def clear_database_by_date(self, date):
        """
        Clear events for a specific date from the database.
        
        Args:
            date (str): Date string in YYYY-MM-DD format
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute('DELETE FROM events WHERE date = ?', (date,))
        
        conn.commit()
        conn.close()
    
    def clear_database_before_date(self, date):
        """
        Clear all events from before a specific date.
        
        Args:
            date (str): Date string in YYYY-MM-DD format
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute('DELETE FROM events WHERE date < ?', (date,))
        
        conn.commit()
        conn.close()
    
    # ---Fetch from database methods---
    def fetch_events_by_date(self, date):
        """
        Fetch all events from the database for a specific date.
        
        Args:
            date (str): Date string in YYYY-MM-DD format
            
        Returns:
            list: List of event dictionaries (column_name: value)
        """
        conn = self._get_connection()
        # Use Row factory so we can easily convert to dicts
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM events WHERE date = ?
        ''', (date,))
        
        rows = cursor.fetchall()
        conn.close()
        
        # Convert sqlite3.Row objects to regular dicts
        return [dict(row) for row in rows]
    
    def fetch_event_by_id(self, event_id):
        """
        Fetch a specific event by its ID.
        
        Args:
            event_id (int): The event ID to fetch
            
        Returns:
            dict: Event data dictionary (column_name: value) or None if not found
        """
        conn = self._get_connection()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM events WHERE event_id = ?
        ''', (event_id,))
        
        row = cursor.fetchone()
        conn.close()
        
        return dict(row) if row else None
    
    # ---Insert into database methods---
    def insert_event(self, event_data):
        """
        Insert a new event into the database.
        
        Args:
            event_data (dict): Dictionary containing event data
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT OR REPLACE INTO events 
            (event_id, league, event_name, date, start_time, end_time, 
             home_team, away_team, home_icon, away_icon, stream_url)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            event_data.get('event_id'),
            event_data.get('league'),
            event_data.get('event_name'),
            event_data.get('date'),
            event_data.get('start_time'),
            event_data.get('end_time'),
            event_data.get('home_team'),
            event_data.get('away_team'),
            event_data.get('home_icon'),
            event_data.get('away_icon'),
            event_data.get('stream_url')
        ))
        
        conn.commit()
        conn.close()

class ESPNDatabaseManager(DatabaseManager):

    def initialize_database(self):
        """
        Initialize the database and create necessary tables.
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # Create table for events from API
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS events (
                event_id INTEGER PRIMARY KEY,
                league TEXT,
                event_name TEXT NOT NULL,
                date TEXT,
                start_time TEXT,
                end_time TEXT,
                home_team TEXT,
                away_team TEXT,
                home_icon TEXT,
                away_icon TEXT
            )
        ''')
        
        conn.commit()
        conn.close()

    # ---Update database methods---
    def update_database(self, date):
        date_formatted = date.replace('-', '')

        scoreboard_urls = {
            'college-football': 'http://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard',
            'nfl': 'http://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard',
            'mlb': 'http://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard',
            'college-baseball': 'https://site.api.espn.com/apis/site/v2/sports/baseball/college-baseball/scoreboard',
            'nhl': 'http://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard',
            'nba': 'http://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard',
            'wnba': 'http://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard',
            'womens-college-basketball': 'http://site.api.espn.com/apis/site/v2/sports/basketball/womens-college-basketball/scoreboard',
            'mens-college-basketball': 'http://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard',
        }

        conn = self._get_connection()
        cursor = conn.cursor()

        for league, url in scoreboard_urls.items():
            try:
                response = requests.get(url, params={'dates': date_formatted})
                response.raise_for_status()
                data = response.json()
                
                if 'events' in data:
                    events = data['events']
                    
                    for event in events:
                        try:
                            event_id = event.get('id')
                            event_name = event.get('name', '')
                            
                            # Parse date and times
                            date_str = event.get('date', '')
                            if date_str:
                                dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                                event_date = dt.strftime('%Y-%m-%d')
                                start_time = dt.strftime('%H:%M:%S')
                            else:
                                event_date = ''
                                start_time = ''
                            
                            end_time = ''  # Not provided in API response
                            
                            # Get team information
                            competitors = event['competitions'][0].get('competitors', [])
                            home_team = ''
                            away_team = ''
                            home_icon = ''
                            away_icon = ''
                            
                            for competitor in competitors:
                                if competitor.get('homeAway') == 'home':
                                    home_team = competitor['team'].get('displayName', '')
                                    home_icon = competitor['team'].get('logo', '')
                                elif competitor.get('homeAway') == 'away':
                                    away_team = competitor['team'].get('displayName', '')
                                    away_icon = competitor['team'].get('logo', '')
                            
                            cursor.execute('''
                                INSERT OR REPLACE INTO events 
                                (event_id, league, event_name, date, start_time, end_time, 
                                 home_team, away_team, home_icon, away_icon)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ''', (event_id, league, event_name, event_date, start_time, end_time,
                                  home_team, away_team, home_icon, away_icon))

                        except Exception as e:
                            print(f"Error processing event {event.get('id', 'unknown')}: {e}")
                            
            except requests.exceptions.RequestException as e:
                print(f"Error fetching {league}: {e}")

        conn.commit()
        conn.close()
    pass