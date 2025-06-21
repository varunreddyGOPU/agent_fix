def buggy_function():
    total = 0
    for i in range(5):
        total += i
    return total

result = buggy_function()
print("The result is:", result)

try:
    print("Attempting to divide 10 by 0.")
    divisor = 0
    if divisor == 0:
        result = float('inf')
        print("Division by zero avoided. Result set to infinity.")
    else:
        result = 10 / divisor
except Exception as e:
    print("An unexpected error occurred.")
    with open('app_error.log', 'a') as log_file:
        log_file.write(f"{type(e).__name__}: {e}\n")

print("Application run finished.")