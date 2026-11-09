from simulator.weather import build_url, parse, save_csv

SAMPLE = {
    "daily": {
        "time": ["2025-07-01", "2025-07-02"],
        "temperature_2m_max": [34.1, 36.0],
        "temperature_2m_min": [22.3, 24.0],
        "temperature_2m_mean": [28.0, 29.9],
        "precipitation_sum": [0.0, 1.4],
        "relative_humidity_2m_mean": [55, 61],
        "et0_fao_evapotranspiration": [6.2, 6.5],
    }
}


def test_url_contains_sfax_and_dates():
    url = build_url(34.74, 10.76, "2025-04-01", "2025-09-30")
    assert "latitude=34.74" in url and "start_date=2025-04-01" in url


def test_parse_one_row_per_day():
    rows = parse(SAMPLE)
    assert len(rows) == 2
    assert rows[1] == {
        "date": "2025-07-02", "t_max": 36.0, "t_min": 24.0, "t_mean": 29.9,
        "rain_mm": 1.4, "rh_mean": 61, "et0_fao": 6.5,
    }


def test_save_csv(tmp_path):
    out = tmp_path / "m.csv"
    save_csv(parse(SAMPLE), out)
    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "date,t_max,t_min,t_mean,rain_mm,rh_mean,et0_fao"
    assert len(lines) == 3