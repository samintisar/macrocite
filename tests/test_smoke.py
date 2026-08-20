def test_package_importable() -> None:
    import signalbench

    assert signalbench.__name__ == "signalbench"
