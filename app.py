import requests
import json
import re
import time
import csv
from collections import defaultdict

# Load configuration
with open('config.json') as config_file:
    config = json.load(config_file)

GITHUB_TOKEN = config['github_token']
ORG_NAME = config['org_name']
COMPLIANT_IMAGES = config['complioant_images']
GRAPHQL_QUERY = config['graphql_query']

HEADERS = {
    'Authorization': f'bearer {GITHUB_TOKEN}',
    'Content-Type': 'application/json'
}

GRAPHQL_URL = "https://api.github.com/graphql"
API_URL = "https://api.github.com"

# Global variable to store statistics
stats = {
    "total_repos": 0,
    "total_images": 0,
    "image_counts": defaultdict(int),
    "compliant_counts": defaultdict(int),
}

build_pipeline_stats = {
    "total_pipeline_images": 0,
    "pipeline_image_counts": defaultdict(int)
}

compliant_images_data = []
non_compliant_images_data = []
build_pipeline_images_data = []

def check_rate_limit():
    """Check the current GitHub API rate limit status."""
    url = "https://api.github.com/rate_limit"
    headers = {'Authorization': f'bearer {GITHUB_TOKEN}'}
    
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        rate_limit_data = response.json()
        remaining = rate_limit_data['rate']['remaining']
        reset_time = rate_limit_data['rate']['reset']
        reset_time_human_readable = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(reset_time))

        print(f"API calls remaining: {remaining}")
        if remaining == 0:
            print(f"Rate limit exceeded. Resets at: {reset_time_human_readable}")
        return remaining
    else:
        print(f"Failed to check rate limit: {response.status_code}")
        return None

def run_query(query, variables):
    """Run a GraphQL query and return the result with retry logic."""
    max_retries = 5
    initial_wait_time = 5  # Start with a 5-second wait time
    backoff_factor = 2
    
    for attempt in range(max_retries):
        try:
            request = requests.post(GRAPHQL_URL, json={'query': query, 'variables': variables}, headers=HEADERS)
            if request.status_code == 200:
                return request.json()
            
            elif request.status_code == 502:
                wait_time = initial_wait_time * (backoff_factor ** attempt)
                print(f"502 Bad Gateway error: Retrying in {wait_time} seconds...")
                time.sleep(wait_time)
            else:
                request.raise_for_status()  # Raise an HTTPError for other 4xx/5xx responses
        except requests.exceptions.HTTPError as http_err:
            raise requests.exceptions.HTTPError(
                f"HTTP error occurred during GraphQL query: {http_err}, Status code: {request.status_code}, Query: {query}"
            ) from http_err
        except Exception as e:
            print(f"Exception occurred during query: {e}")
            if attempt == max_retries - 1:
                raise  # Re-raise the last exception if max retries reached

    raise requests.exceptions.RequestException(f"Max retries exceeded. Last response code: {request.status_code}")

def get_dockerfile_content(repo_name, branch_name):
    """Fetch the content of Dockerfiles from the default branch of a repository."""
    url = f"{API_URL}/repos/{ORG_NAME}/{repo_name}/contents/"
    headers = {'Authorization': f'bearer {GITHUB_TOKEN}'}
    params = {'ref': branch_name}
    
    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()  # Raise an HTTPError for bad responses (4xx, 5xx)
        files = response.json()
        for file in files:
            if file['name'].lower().startswith('dockerfile'):
                dockerfile_response = requests.get(file['download_url'], headers=headers)
                dockerfile_response.raise_for_status()  # Raise an error for bad responses
                return dockerfile_response.text
    except requests.exceptions.HTTPError as http_err:
        print(f"Failed to fetch files for repo: {repo_name}, branch: {branch_name} - HTTP error occurred: {http_err}")
    except requests.exceptions.RequestException as req_err:
        print(f"Failed to fetch files for repo: {repo_name}, branch: {branch_name} - Request error occurred: {req_err}")
    except Exception as e:
        print(f"Failed to fetch files for repo: {repo_name}, branch: {branch_name} - Unexpected error: {e}")

    return None

def get_file_content(repo_name, branch_name, file_path):
    """Fetch the content of specific files in the repository."""
    url = f"{API_URL}/repos/{ORG_NAME}/{repo_name}/contents/{file_path}"
    headers = {'Authorization': f'bearer {GITHUB_TOKEN}'}
    params = {'ref': branch_name}
    
    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()  # Raise an error for bad responses
        file_data = response.json()

        if isinstance(file_data, dict) and 'download_url' in file_data:
            file_response = requests.get(file_data['download_url'], headers=headers)
            file_response.raise_for_status()  # Raise an error for bad responses
            return file_response.text
    except requests.exceptions.HTTPError as http_err:
        print(f"Failed to fetch {file_path} for repo: {repo_name}, branch: {branch_name} - HTTP error occurred: {http_err}")
    except requests.exceptions.RequestException as req_err:
        print(f"Failed to fetch {file_path} for repo: {repo_name}, branch: {branch_name} - Request error occurred: {req_err}")
    except Exception as e:
        print(f"Failed to fetch {file_path} for repo: {repo_name}, branch: {branch_name} - Unexpected error: {e}")
    
    return None

def find_relevant_files(repo_name, branch_name):
    """Find relevant files in a repository."""
    relevant_files = []
    url = f"{API_URL}/repos/{ORG_NAME}/{repo_name}/git/trees/{branch_name}?recursive=1"
    headers = {'Authorization': f'bearer {GITHUB_TOKEN}'}
    
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()  # Raise an error for bad responses
        tree_data = response.json()

        for item in tree_data.get('tree', []):
            path = item['path']
            if path.lower().endswith(('docker-compose.yml', 'docker-compose.yaml', '.gitlab-ci.yml', '.github/workflows', '.concourse', 'pipeline')):
                relevant_files.append(path)
                
    except requests.exceptions.HTTPError as http_err:
        print(f"Failed to fetch tree for repo: {repo_name}, branch: {branch_name} - HTTP error occurred: {http_err}")
    except requests.exceptions.RequestException as req_err:
        print(f"Failed to fetch tree for repo: {repo_name}, branch: {branch_name} - Request error occurred: {req_err}")
    except Exception as e:
        print(f"Failed to fetch tree for repo: {repo_name}, branch: {branch_name} - Unexpected error: {e}")

    return relevant_files

def process_relevant_files(repo_name, branch_name, file_content, file_path):
    """Process the content of docker-compose and CI files."""
    docker_image_patterns = [
        r'(?i)image:\s*([^\s]+)',
        r'(?i)services:\s*-\s*name:\s*([^\s]+)',
        r'(?i)repository:\s*([^\s]+)',
        r'(?i)resource_types:\s*- name:\s*[^\n]+source:\s*repository:\s*([^\s]+)'
    ]
    
    for pattern in docker_image_patterns:
        matches = re.findall(pattern, file_content)
        if matches:
            for match in matches:
                full_image_name, image_name = resolve_parameterized_image(match, {})
                record_pipeline_image(repo_name, branch_name, image_name, full_image_name, file_path)

def scan_repositories():
    """Scan repositories in the GitHub organization and process Dockerfiles."""
    if check_rate_limit() == 0:
        return  # Exit if rate limit has been exceeded

    variables = {"orgName": ORG_NAME}
    process_all_repositories(variables)

def process_all_repositories(variables):
    """Process all repositories using pagination."""
    has_next_page = True
    after_cursor = None

    while has_next_page:
        repositories, page_info = fetch_repositories(variables, after_cursor)
        stats["total_repos"] += len(repositories)

        process_each_repository(repositories)

        has_next_page = page_info['hasNextPage']
        after_cursor = page_info['endCursor']

        if check_rate_limit() == 0:
            print("Rate limit reached. Exiting to wait for reset.")
            break

def fetch_repositories(variables, after_cursor):
    """Fetch repositories from the GitHub organization."""
    if after_cursor:
        variables['afterCursor'] = after_cursor
    result = run_query(GRAPHQL_QUERY, variables)
    repositories = result['data']['organization']['repositories']['edges']
    page_info = result['data']['organization']['repositories']['pageInfo']
    return repositories, page_info

def process_each_repository(repositories):
    """Process each repository."""
    for repo in repositories:
        repo_name = repo['node']['name']
        if repo['node']['isArchived']:
            print(f"Skipping archived repo: {repo_name}")
            continue

        branch_name = get_default_branch(repo)
        if branch_name:
            dockerfile_content = get_dockerfile_content(repo_name, branch_name)
            if dockerfile_content:
                process_dockerfiles(repo_name, branch_name, dockerfile_content)

            # New block to find and process relevant files
            relevant_files = find_relevant_files(repo_name, branch_name)
            for file_path in relevant_files:
                file_content = get_file_content(repo_name, branch_name, file_path)
                if file_content:
                    process_relevant_files(repo_name, branch_name, file_content, file_path)

def get_default_branch(repo):
    """Get the default branch name of a repository."""
    return repo['node']['defaultBranchRef']['name'] if repo['node']['defaultBranchRef'] else None

def process_dockerfiles(repo_name, branch_name, dockerfile_content):
    """Process the Dockerfile content."""
    if not dockerfile_content:
        return
    
    args = extract_args(dockerfile_content)
    from_matches = find_from_directives(dockerfile_content)
    recorded_images = set()

    if from_matches:
        for image_line in from_matches:
            process_image_line(image_line, args, recorded_images, repo_name, branch_name, "Dockerfile")

def process_image_line(image_line, args, recorded_images, repo_name, branch_name, file_path):
    """Process each image line found in the Dockerfile or other relevant files."""
    if '--platform=$BUILDPLATFORM' in image_line:
        return
    
    full_image_name, image_name = resolve_parameterized_image(image_line, args)

    if image_name in ['base', 'build', 'final', 'builder']:
        process_multistage_images(image_name, recorded_images, repo_name, branch_name, full_image_name, file_path)
    elif '${' not in full_image_name:
        record_image(repo_name, branch_name, image_name, full_image_name, file_path)

def process_multistage_images(image_name, recorded_images, repo_name, branch_name, full_image_name, file_path):
    """Process multistage build images."""
    if image_name not in recorded_images:
        recorded_images.add(image_name)
        record_image(repo_name, branch_name, image_name, full_image_name, file_path)

def extract_args(dockerfile_content):
    """Extract ARG directives from Dockerfile content."""
    arg_pattern = re.compile(r'ARG\s+([A-Z_]+)=([^\s]+)')
    return dict(arg_pattern.findall(dockerfile_content))

def find_from_directives(dockerfile_content):
    """Find FROM directives in Dockerfile content."""
    from_pattern = re.compile(r'FROM\s+([^\s]+)')
    return from_pattern.findall(dockerfile_content)

def resolve_parameterized_image(image_line, args):
    """Resolve parameterized image names."""
    if '${' in image_line:
        for arg, value in args.items():
            image_line = image_line.replace(f'${{{arg}}}', value)
    full_image_name = image_line.split('/')[-1]
    image_name = full_image_name.split(':')[0]
    return full_image_name, image_name

def record_image(repo_name, branch_name, image_name, full_image_name, file_path):
    """Record the image as compliant or non-compliant."""
    # Skip images with the tag "local"
    if ":local" in full_image_name:
        print(f"Skipping image {full_image_name} as it contains the 'local' tag.")
        return

    stats["total_images"] += 1
    stats["image_counts"][image_name] += 1

    # Skip certain non-compliant image names
    if image_name in ['base', 'final', 'builder', 'amd64', 'arm64']:
        print(f"Skipping image {full_image_name} as it is categorized as a non-compliant placeholder.")
        return

    if is_compliant(image_name):
        compliant = '', 
        if image_name in COMPLIANT_IMAGES:
            stats["compliant_counts"][image_name] += 1
            compliant = 'X'

        compliant_images_data.append([repo_name, branch_name, full_image_name, compliant])
        print(f"Repo: {repo_name}, Branch: {branch_name}, Image: {image_name}, Full Image: {full_image_name}")
    else:
        print(f"Repo: {repo_name}, Branch: {branch_name}, Image: {full_image_name} is non-compliant.")
        non_compliant_images_data.append([repo_name, branch_name, full_image_name, file_path])

def record_pipeline_image(repo_name, branch_name, image_name, full_image_name, file_path):
    """Record the image found in build pipelines."""
    build_pipeline_stats["total_pipeline_images"] += 1
    build_pipeline_stats["pipeline_image_counts"][image_name] += 1
    build_pipeline_images_data.append([repo_name, branch_name, full_image_name, file_path])
    print(f"Repo: {repo_name}, Branch: {branch_name}, Pipeline Image: {image_name}, Full Image: {full_image_name}, File: {file_path}")

def is_compliant(image_name):
    """Check if the image is compliant."""
    resolved_image_name = image_name.split(':')[0]  # Get base image name without tag
    return (resolved_image_name in COMPLIANT_IMAGES)

def print_information():
    total_repos = stats["total_repos"]
    total_images = stats["total_images"]
    compliant_total, total_compliant_images = calculate_totals()

    print_general_statistics(total_repos, total_images, total_compliant_images)
    print_compliant_image_statistics(compliant_total, "Compliant", stats["compliant_counts"])

def print_pipeline_information():
    """Print statistics for images found in build pipelines."""
    total_pipeline_images = build_pipeline_stats["total_pipeline_images"]
    print("\n--- Build Pipeline Images Statistics ---")
    print(f"Total number of pipeline images: {total_pipeline_images}")

    if total_pipeline_images > 0:
        print("Pipeline Image Breakdown:")
        for image, count in build_pipeline_stats["pipeline_image_counts"].items():
            percentage = (count / total_pipeline_images) * 100
            print(f"{image}: {count} ({percentage:.2f}%)")
    else:
        print("No pipeline images found.")

def calculate_totals():
    """Calculate total compliant images across categories."""
    compliant_total = sum(stats["compliant_counts"].values())

    total_compliant_images = compliant_total 
    return compliant_total, total_compliant_images

def print_general_statistics(total_repos, total_images, total_compliant_images):
    """Print general statistics about the repositories and images."""
    print("\n--- Statistics ---")
    print(f"Total number of repos: {total_repos}")
    print(f"Total number of Docker images: {total_images}")

    if total_images > 0:
        compliant_percentage = (total_compliant_images / total_images) * 100
        print(f"Total number of compliant images: {total_compliant_images} ({compliant_percentage:.2f}% of total images)")
    else:
        print("Total number of compliant images: 0 (0.00% of total images)")

def print_compliant_image_statistics(total, label, compliant_counts):
    """Print the compliant image statistics for a specific category."""
    if total > 0:
        print(f"\n{label} Compliant Images:")
        for image, count in compliant_counts.items():
            percentage = (count / total) * 100 if total > 0 else 0
            print(f"{image}: {count} ({percentage:.2f}%)")

def compliant_images():
    """Create a CSV with compliant images and their corresponding categories."""
    with open('compliant_images.csv', mode='w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(["Repo name", "Repo branch", "Image name"])
        writer.writerows(compliant_images_data)
    print("Compliant images CSV generated: compliant_images.csv")

def non_compliant_images():
    """Create a CSV with non-compliant images including top contributors and image path."""
    updated_non_compliant_images_data = []

    for i in range(len(non_compliant_images_data)):
        repo_name, branch_name, image_name = non_compliant_images_data[i][:3]
        image_path = non_compliant_images_data[i][3]  # Retrieve the image path
        
        top_contribs = top_contributors(repo_name, branch_name)
        top_contribs_str = ', '.join(top_contribs)  

        updated_row = [repo_name, branch_name, image_name, top_contribs_str, image_path]  
        updated_non_compliant_images_data.append(updated_row)

    with open('non_compliant_images.csv', mode='w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(["Repo name", "Repo branch", "Image name", "Top Contributors", "Image path"])
        writer.writerows(updated_non_compliant_images_data)

    print("Non-compliant images CSV generated: non_compliant_images.csv")

def build_pipeline_images():
    """Create a CSV with images found in build pipelines."""
    with open('build_pipeline_images.csv', mode='w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(["Repo name", "Repo branch", "Image name", "File path"])
        writer.writerows(build_pipeline_images_data)
    print("Build pipeline images CSV generated: build_pipeline_images.csv")

def top_contributors(repo_name, branch_name):
    """Get the top 5 contributors sorted by recent activity and most commits for a given repo."""
    url = f"{API_URL}/repos/{ORG_NAME}/{repo_name}/commits"
    headers = {'Authorization': f'bearer {GITHUB_TOKEN}'}
    params = {'sha': branch_name, 'per_page': 100}

    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()

        commits = response.json()
        contributor_counts = defaultdict(int)

        for commit in commits:
            author = commit['commit']['author']['name']
            contributor_counts[author] += 1

        sorted_contributors = sorted(contributor_counts.items(), key=lambda x: (-x[1], x[0]))

        return [contributor for contributor, _ in sorted_contributors[:5]]

    except requests.exceptions.RequestException as e:
        print(f"Error fetching top contributors for repo: {repo_name}, branch: {branch_name} - {e}")
        return []

if __name__ == "__main__":
    scan_repositories()
    print_information()
    print_pipeline_information()
    compliant_images()  
    non_compliant_images()
    build_pipeline_images()
