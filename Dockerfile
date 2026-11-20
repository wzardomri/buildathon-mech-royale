FROM python:3.13-slim
WORKDIR /bot
COPY mechfight.py route_bot.py weapon_control.py laser_defense.py ./
ENTRYPOINT ["python3", "-B", "-u", "/bot/route_bot.py"]
