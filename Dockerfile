FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
WORKDIR /bot
COPY market_survey.py mechfight.py route_bot.py weapon_control.py combat_bot.py projectile_evasion.py colliding_route.py collision_edges.json ./
ENTRYPOINT ["python3", "-B", "-u", "/bot/combat_bot.py"]
