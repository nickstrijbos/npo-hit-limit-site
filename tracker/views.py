from django.shortcuts import render
import pandas as pd

def index_view(request):
    context = {
        'limit_24h': 15,
        'limit_48h': 25,
        'defender_faction': '',
        'results': None,
        'error': None
    }

    if request.method == 'POST':
        csv_file = request.FILES.get('csv_file')
        
        # KEEP STATE: Grab the values so we can pass them right back to the form
        limit_24h = int(request.POST.get('limit_24h', 15))
        limit_48h = int(request.POST.get('limit_48h', 25))
        defender_faction = request.POST.get('defender_faction', '').strip()
        
        # Update context immediately so the form doesn't clear if there's an error
        context.update({
            'limit_24h': limit_24h,
            'limit_48h': limit_48h,
            'defender_faction': defender_faction,
        })
        
        if csv_file:
            try:
                # 1. Try reading with standard commas
                df = pd.read_csv(csv_file)
                
                # 2. Try semicolons if needed
                if 'timestamp_started' not in df.columns:
                    csv_file.seek(0)
                    df = pd.read_csv(csv_file, sep=';')
                
                if 'timestamp_started' not in df.columns:
                    raise ValueError("Could not find 'timestamp_started' column. Invalid YATA export.")

                # Force Attacker to be NPO
                df = df[df['attacker_factionname'].astype(str).str.contains('NPO', case=False, na=False)]

                # Filter by Defender Faction (solves the "outside hits" problem)
                if defender_faction:
                    df = df[df['defender_factionname'].astype(str).str.contains(defender_faction, case=False, na=False)]

                if df.empty:
                    raise ValueError(f"No NPO attacks found against '{defender_faction}'.")

                # Sort to find the true start time of the war
                df = df.sort_values(by='timestamp_started')
                war_start_time = df['timestamp_started'].min()

                # Filter valid hits for tickets (Attacked, Assist, AND Lost)
                df_valid = df[df['result'].isin(['Attacked', 'Assist', 'Lost'])].copy()
                
                if df_valid.empty:
                    raise ValueError(f"No valid attacks found against '{defender_faction}'.")

                df_valid['hours_since_start'] = (df_valid['timestamp_started'] - war_start_time) / 3600

                results = []
                grouped = df_valid.groupby(['attacker_id', 'attacker_name'])
                
                for (attacker_id, attacker_name), group in grouped:
                    
                    # --- LIMIT CALCULATOR (Only Attacks + Assists) ---
                    limit_group = group[group['result'].isin(['Attacked', 'Assist'])]
                    hits_24h = len(limit_group[limit_group['hours_since_start'] <= 24])
                    hits_48h = len(limit_group[limit_group['hours_since_start'] <= 48])
                    
                    # --- TICKET CALCULATOR (Breakdown) ---
                    attacks = len(group[group['result'] == 'Attacked'])
                    assists = len(group[group['result'] == 'Assist'])
                    losses = len(group[group['result'] == 'Lost'])

                    # 2/3 Rule Logic: Losses must be <= 2x Assists
                    # (e.g. 1 assist allows up to 2 paid losses. If 3 losses, 1 goes unpaid).
                    paid_losses = losses if losses <= (assists * 2) else (assists * 2)
                    
                    # Calculate total tickets
                    tickets = (attacks * 20) + (assists * 15) + (paid_losses * 15)

                    results.append({
                        'id': attacker_id,
                        'name': attacker_name,
                        'hits_24h': hits_24h,
                        'over_24h': hits_24h > limit_24h,
                        'hits_48h': hits_48h,
                        'over_48h': hits_48h > limit_48h,
                        'attacks': attacks,
                        'assists': assists,
                        'losses': losses,
                        'paid_losses': paid_losses,
                        'tickets': tickets
                    })

                # Sort by Tickets descending
                results = sorted(results, key=lambda x: x['tickets'], reverse=True)

                context['results'] = results

            except Exception as e:
                context['error'] = f"Error: {str(e)}"
                print(f"Error: {e}")

    return render(request, 'tracker/index.html', context)