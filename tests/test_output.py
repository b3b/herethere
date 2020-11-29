from herethere.there.output import LimitedOutput


def test_maxlen_number_of_lines_displayed():
    output = LimitedOutput(maxlen=2)
    output.write("1\n")
    output.write("2\n")
    assert [line['text'] for line in output.outputs] == ['1\n', '2\n']
    output.write("3\n")
    assert [line['text'] for line in output.outputs] == ['2\n', '3\n']
