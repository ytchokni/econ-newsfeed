from fetch_and_save_html import add_urls_for_researcher, fetch_and_save_urls

def main():
    while True:
        choice = input("Enter '1' to add URLs for a researcher, '2' to fetch and save HTML, or '3' to exit: ")
        if choice == '1':
            add_urls_for_researcher()
        elif choice == '2':
            fetch_and_save_urls()
        elif choice == '3':
            print("Exiting the program.")
            break
        else:
            print("Invalid choice. Please try again.")

if __name__ == "__main__":
    main()