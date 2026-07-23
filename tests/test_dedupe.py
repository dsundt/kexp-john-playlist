from kexp.dedupe import classify


def test_classify_paths():
    items = [
        {"position": 0, "uri": "u0", "id": "a", "isrc": "I1", "artist": "X", "name": "Song"},
        {"position": 1, "uri": "u1", "id": "a", "isrc": "I1", "artist": "X", "name": "Song"},      # exact id -> remove 1
        {"position": 2, "uri": "u2", "id": "b", "isrc": "I2", "artist": "Y", "name": "Feeling Good"},
        {"position": 3, "uri": "u3", "id": "c", "isrc": "I2", "artist": "Y", "name": "Feeling Good"},  # same ISRC -> remove 3
        {"position": 4, "uri": "u4", "id": "d", "isrc": "I3", "artist": "Z", "name": "Stop"},
        {"position": 5, "uri": "u5", "id": "e", "isrc": "I4", "artist": "Z", "name": "Stop!"},         # safe near -> remove 5
        {"position": 6, "uri": "u6", "id": "f", "isrc": "I5", "artist": "R", "name": "Finest Worksong"},
        {"position": 7, "uri": "u7", "id": "g", "isrc": "I6", "artist": "R", "name": "Finest Worksong - Remastered"},  # report
    ]
    plan = classify(items)
    assert plan.remove_positions == [1, 3, 5]
    assert plan.counts == {"exact": 1, "isrc": 1, "safe_near": 1, "reported": 1}
    assert len(plan.report) == 1 and plan.report[0]["artist"] == "R"
