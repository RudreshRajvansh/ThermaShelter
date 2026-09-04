import requests

latitude = 34.16
longitude = 77.58

parameters = (
    "T2M,RH2M,WS2M,PS,"
    "ALLSKY_SFC_SW_DWN,"
    "ALLSKY_SFC_SW_DNI,"
    "ALLSKY_SFC_SW_DIFF"
)

url = (
    "https://power.larc.nasa.gov/api/temporal/hourly/point"
    f"?parameters={parameters}"
    "&community=SB"
    f"&longitude={longitude}"
    f"&latitude={latitude}"
    "&start=20170101"
    "&end=20251231"
    "&format=CSV"
)

print("Downloading NASA POWER weather data...")

response = requests.get(url)

if response.status_code == 200:
    with open("leh_weather_2017_2025.csv", "wb") as file:
        file.write(response.content)

    print("DONE! Weather data downloaded.")
else:
    print("Download failed.")
    print("Error code:", response.status_code)