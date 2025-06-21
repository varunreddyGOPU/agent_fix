def buggy_function():
    total = 0
    for i in range(5):
        total += i
    return total

def application_run():
    print("Application starting...")
    print("Performing calculation...")
    
    try:
        divisor = 2 #Fixed: Changed divisor to avoid division by zero
        result = 10 / divisor
        print(f"The result is: {result}")
    except ZeroDivisionError as e:
        print(f"Caught ZeroDivisionError: {e}")
        # In a real app, this would be logged. We simulate it below.
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
    finally:
        print("Application run finished.")

if __name__ == "__main__":
    application_run()