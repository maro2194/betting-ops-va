"""
Player → Team mappings for bet365 match lookup.
bet365 shows team names in match listings (e.g. "Rockets", "Lakers").
We need to know which team a player is on to find their game.
"""

# player_name (lowercase) -> bet365 team display name
PLAYER_TEAMS: dict[str, str] = {}

# ─── NBA ─────────────────────────────────────────────────────────────────────

_NBA_ROSTERS = {
    "Hawks": ["Trae Young", "Dejounte Murray", "Bogdan Bogdanovic", "Clint Capela", "De'Andre Hunter", "Jalen Johnson"],
    "Celtics": ["Jayson Tatum", "Jaylen Brown", "Kristaps Porzingis", "Derrick White", "Jrue Holiday"],
    "Nets": ["Mikal Bridges", "Ben Simmons", "Cam Thomas", "Nic Claxton", "Dennis Schroder"],
    "Hornets": ["LaMelo Ball", "Brandon Miller", "Mark Williams", "Miles Bridges"],
    "Bulls": ["Zach LaVine", "DeMar DeRozan", "Nikola Vucevic", "Coby White"],
    "Cavaliers": ["Donovan Mitchell", "Darius Garland", "Evan Mobley", "Jarrett Allen", "Luke Kornet"],
    "Pistons": ["Cade Cunningham", "Jaden Ivey", "Jalen Duren", "Ausar Thompson"],
    "Pacers": ["Tyrese Haliburton", "Myles Turner", "Pascal Siakam", "Buddy Hield", "Andrew Nembhard"],
    "Heat": ["Bam Adebayo", "Tyler Herro", "Terry Rozier", "Caleb Martin"],
    "Bucks": ["Giannis Antetokounmpo", "Damian Lillard", "Khris Middleton", "Brook Lopez", "Bobby Portis"],
    "Knicks": ["Jalen Brunson", "Karl-Anthony Towns", "OG Anunoby", "Josh Hart", "Mikal Bridges"],
    "Magic": ["Paolo Banchero", "Franz Wagner", "Jalen Suggs", "Wendell Carter Jr"],
    "76ers": ["Joel Embiid", "Tyrese Maxey", "Kelly Oubre Jr", "Paul George"],
    "Raptors": ["Scottie Barnes", "RJ Barrett", "Immanuel Quickley", "Jakob Poeltl"],
    "Wizards": ["Jordan Poole", "Kyle Kuzma", "Bilal Coulibaly"],
    "Mavericks": ["Luka Doncic", "Kyrie Irving", "PJ Washington", "Dereck Lively"],
    "Nuggets": ["Nikola Jokic", "Jamal Murray", "Michael Porter Jr", "Aaron Gordon"],
    "Warriors": ["Stephen Curry", "Andrew Wiggins", "Draymond Green", "Jonathan Kuminga", "Jimmy Butler"],
    "Rockets": ["Jalen Green", "Alperen Sengun", "Jabari Smith Jr", "Fred VanVleet", "Amen Thompson", "Reed Sheppard", "Tari Eason"],
    "Clippers": ["James Harden", "Kawhi Leonard", "Ivica Zubac", "Norman Powell"],
    "Lakers": ["LeBron James", "Anthony Davis", "Austin Reaves", "Dalton Knecht", "Rui Hachimura"],
    "Grizzlies": ["Ja Morant", "Desmond Bane", "Jaren Jackson Jr", "Marcus Smart", "Jake LaRavia"],
    "Timberwolves": ["Anthony Edwards", "Rudy Gobert", "Mike Conley", "Jaden McDaniels", "Julius Randle"],
    "Pelicans": ["Zion Williamson", "Brandon Ingram", "CJ McCollum", "Herb Jones"],
    "Thunder": ["Shai Gilgeous-Alexander", "Jalen Williams", "Chet Holmgren", "Luguentz Dort"],
    "Suns": ["Kevin Durant", "Devin Booker", "Bradley Beal", "Jusuf Nurkic"],
    "Trail Blazers": ["Anfernee Simons", "Jerami Grant", "Deandre Ayton", "Shaedon Sharpe"],
    "Kings": ["De'Aaron Fox", "Domantas Sabonis", "Keegan Murray", "Malik Monk"],
    "Spurs": ["Victor Wembanyama", "Devin Vassell", "Keldon Johnson", "Tre Jones"],
    "Jazz": ["Lauri Markkanen", "Collin Sexton", "Jordan Clarkson", "John Collins", "Walker Kessler"],
}

# ─── AFL ─────────────────────────────────────────────────────────────────────

_AFL_ROSTERS = {
    "Brisbane": ["Lachie Neale", "Dayne Zorko", "Charlie Cameron", "Hugh McCluggage", "Josh Dunkley", "Will Ashcroft", "Logan Morris"],
    "Collingwood": ["Nick Daicos", "Josh Daicos", "Jordan De Goey", "Scott Pendlebury", "Darcy Moore", "Jack Crisp", "Bobby Hill"],
    "Carlton": ["Patrick Cripps", "Charlie Curnow", "Sam Walsh", "Jacob Weitering", "Adam Saad"],
    "Richmond": ["Dustin Martin", "Tom Lynch", "Shai Bolton", "Daniel Rioli", "Dion Prestia"],
    "Melbourne": ["Christian Petracca", "Clayton Oliver", "Max Gawn", "Jake Lever", "Bayley Fritsch"],
    "Geelong": ["Tom Hawkins", "Jeremy Cameron", "Patrick Dangerfield", "Tom Stewart"],
    "Sydney": ["Isaac Heeney", "Callum Mills", "Luke Parker", "Chad Warner", "Errol Gulden"],
    "Hawthorn": ["James Worpel", "Jai Newcombe", "Jack Gunston", "James Sicily"],
    "West Coast": ["Tim Kelly", "Andrew Gaff", "Liam Duggan", "Jeremy McGovern"],
    "Fremantle": ["Andrew Brayshaw", "Caleb Serong", "Hayden Young", "Alex Pearce", "Nat Fyfe"],
    "Essendon": ["Zach Merrett", "Darcy Parish", "Jake Stringer", "Jordan Ridley"],
    "North Melbourne": ["Luke Davies-Uniacke", "Jason Horne-Francis", "Nick Larkey", "Harry Sheezel"],
    "Western Bulldogs": ["Marcus Bontempelli", "Tom Liberatore", "Adam Treloar", "Bailey Smith"],
    "St Kilda": ["Jack Sinclair", "Jack Steele", "Rowan Marshall", "Max King"],
    "GWS": ["Toby Greene", "Josh Kelly", "Stephen Coniglio", "Jesse Hogan"],
    "Gold Coast": ["Matt Rowell", "Noah Anderson", "Jarrod Witts", "Touk Miller", "Ben King"],
    "Adelaide": ["Rory Laird", "Ben Keays", "Jordan Dawson", "Taylor Walker", "Izak Rankine"],
    "Port Adelaide": ["Connor Rozee", "Zak Butters", "Travis Boak", "Ollie Wines"],
}

# ─── MLB ─────────────────────────────────────────────────────────────────────

_MLB_ROSTERS = {
    "Yankees": ["Aaron Judge", "Juan Soto", "Anthony Volpe", "Giancarlo Stanton"],
    "Dodgers": ["Shohei Ohtani", "Mookie Betts", "Freddie Freeman", "Will Smith"],
    "Astros": ["Yordan Alvarez", "Jose Altuve", "Alex Bregman", "Kyle Tucker"],
    "Braves": ["Ronald Acuna Jr", "Matt Olson", "Austin Riley", "Ozzie Albies"],
    "Phillies": ["Bryce Harper", "Trea Turner", "Kyle Schwarber", "JT Realmuto"],
    "Mets": ["Francisco Lindor", "Pete Alonso", "Brandon Nimmo", "Jeff McNeil"],
    "Red Sox": ["Rafael Devers", "Masataka Yoshida", "Trevor Story"],
    "Padres": ["Fernando Tatis Jr", "Manny Machado", "Xander Bogaerts"],
    "Twins": ["Austin Martin", "Byron Buxton", "Carlos Correa", "Royce Lewis"],
    "Mariners": ["Julio Rodriguez", "Cal Raleigh", "JP Crawford"],
    "Orioles": ["Gunnar Henderson", "Adley Rutschman", "Anthony Santander"],
    "Rangers": ["Corey Seager", "Marcus Semien", "Adolis Garcia"],
    "Blue Jays": ["Vladimir Guerrero Jr", "Bo Bichette", "George Springer"],
    "Cubs": ["Dansby Swanson", "Cody Bellinger", "Ian Happ"],
    "Guardians": ["Jose Ramirez", "Josh Naylor", "Steven Kwan"],
    "Diamondbacks": ["Ketel Marte", "Corbin Carroll", "Christian Walker"],
    "White Sox": ["Luis Robert Jr", "Andrew Vaughn", "Eloy Jimenez"],
    "Royals": ["Bobby Witt Jr", "Salvador Perez", "Vinnie Pasquantino"],
}

# ─── NRL ─────────────────────────────────────────────────────────────────────

_NRL_ROSTERS = {
    "Broncos": ["Reece Walsh", "Patrick Carrigan", "Payne Haas", "Adam Reynolds"],
    "Panthers": ["Nathan Cleary", "Isaah Yeo", "Brian To'o", "Dylan Edwards"],
    "Storm": ["Cameron Munster", "Jahrome Hughes", "Harry Grant", "Ryan Papenhuyzen"],
    "Roosters": ["James Tedesco", "Brandon Smith", "Luke Keary", "Joseph Suaalii"],
    "Rabbitohs": ["Latrell Mitchell", "Cody Walker", "Cameron Murray", "Damien Cook"],
    "Sharks": ["Nicho Hynes", "Jesse Ramien", "Blayke Brailey", "Cameron McInnes"],
    "Cowboys": ["Valentine Holmes", "Scott Drinkwater", "Jason Taumalolo", "Tom Dearden"],
    "Raiders": ["Jack Wighton", "Hudson Young", "Josh Papali'i", "Joseph Tapine"],
    "Sea Eagles": ["Tom Trbojevic", "Daly Cherry-Evans", "Haumole Olakau'atu"],
    "Eels": ["Mitchell Moses", "Clint Gutherson", "Dylan Brown", "Junior Paulo"],
    "Dragons": ["Ben Hunt", "Zac Lomax", "Jack de Belin", "Jaydn Su'A"],
    "Titans": ["David Fifita", "AJ Brimson", "Tino Fa'asuamaleaui"],
    "Knights": ["Kalyn Ponga", "Bradman Best", "Tyson Frizell"],
    "Warriors": ["Shaun Johnson", "Addin Fonua-Blake", "Tohu Harris"],
    "Bulldogs": ["Matt Burton", "Josh Addo-Carr", "Stephen Crichton"],
    "Tigers": ["Apisai Koroisau", "David Klemmer", "Adam Doueihi"],
}

# ─── Build flat lookup ───────────────────────────────────────────────────────

def _build():
    for rosters in [_NBA_ROSTERS, _AFL_ROSTERS, _MLB_ROSTERS, _NRL_ROSTERS]:
        for team, players in rosters.items():
            for player in players:
                PLAYER_TEAMS[player.lower().strip()] = team

_build()


def find_team(player_name: str, sport: str = "") -> str | None:
    """Find a player's team. Returns the team display name or None."""
    key = player_name.lower().strip()

    # Direct lookup
    if key in PLAYER_TEAMS:
        return PLAYER_TEAMS[key]

    # Partial match
    for name, team in PLAYER_TEAMS.items():
        if name in key or key in name:
            return team

    # Last name match (only if unique)
    last = key.split()[-1]
    if len(last) > 2:
        matches = list({t for n, t in PLAYER_TEAMS.items() if last in n.split()})
        if len(matches) == 1:
            return matches[0]

    return None
