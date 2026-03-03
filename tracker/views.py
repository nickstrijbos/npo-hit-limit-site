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
        
        if csv_file:
            limit_24h = int(request.POST.get('limit_24h', 15))
            limit_48h = int(request.POST.get('limit_48h', 25))
            defender_faction = request.POST.get('defender_faction', '').strip()
            
            try:
                # 1. Try reading with standard commas
                df = pd.read_csv(csv_file)
                
                # 2. If the column isn't there, it might be semicolon separated
                if 'timestamp_started' not in df.columns:
                    csv_file.seek(0)
                    df = pd.read_csv(csv_file, sep=';')
                
                # 3. If it's STILL not there, the CSV is invalid
                if 'timestamp_started' not in df.columns:
                    raise ValueError("Could not find 'timestamp_started' column. Is this a valid Torn export?")

                # --- NEW FILTERING LOGIC ---
                # Force Attacker to be NPO (handles NPO - Prosperity, etc.)
                df = df[df['attacker_factionname'].astype(str).str.contains('NPO', case=False, na=False)]

                # Filter by Defender Faction if the user typed one in
                if defender_faction:
                    df = df[df['defender_factionname'].astype(str).str.contains(defender_faction, case=False, na=False)]

                # Check if the dataframe is empty after filtering
                if df.empty:
                    raise ValueError(f"No NPO attacks found against '{defender_faction}'. Check your spelling or the CSV file.")

                # Sort to find the true start time of the war against THIS specific faction
                df = df.sort_values(by='timestamp_started')
                war_start_time = df['timestamp_started'].min()

                # Filter valid hits (Attacked and Assist)
                df_valid = df[df['result'].isin(['Attacked', 'Assist'])].copy()
                
                # If no valid hits (only lost/stalemate), handle it
                if df_valid.empty:
                    raise ValueError(f"No valid attacks (Attacked/Assist) found against '{defender_faction}'.")

                # Calculate hours elapsed for each hit
                df_valid['hours_since_start'] = (df_valid['timestamp_started'] - war_start_time) / 3600

                results = []
                grouped = df_valid.groupby(['attacker_id', 'attacker_name'])
                
                for (attacker_id, attacker_name), group in grouped:
                    hits_24h = len(group[group['hours_since_start'] <= 24])
                    hits_48h = len(group[group['hours_since_start'] <= 48])
                    total_hits = len(group)

                    results.append({
                        'id': attacker_id,
                        'name': attacker_name,
                        'hits_24h': hits_24h,
                        'over_24h': hits_24h > limit_24h,
                        'hits_48h': hits_48h,
                        'over_48h': hits_48h > limit_48h,
                        'total_hits': total_hits
                    })

                # Sort by highest total hits
                results = sorted(results, key=lambda x: x['total_hits'], reverse=True)

                # Update context to render the results
                context.update({
                    'results': results,
                    'limit_24h': limit_24h,
                    'limit_48h': limit_48h,
                    'defender_faction': defender_faction, # Keeps the input filled in after submit
                })

            except Exception as e:
                context['error'] = f"Error: {str(e)}"
                print(f"Error: {e}")

    return render(request, 'tracker/index.html', context)