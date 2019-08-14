#!/bin/sh

set -eu

docker_binary_path="$(which docker)"
docker_json_path="${HOME}/.docker/config.json"
nvidia_docker_binary_path="$(which nvidia-docker)"

# process given arguments
while test "${#}" -gt 0; do
	case "${1}" in
		--docker-binary-path)
			shift
			docker_binary_path="${1}"
		;;
    --config-directory)
            shift
      config_directory="${1}"
		;;
		--docker-json-path)
			shift
			docker_json_path="${1}"
		;;
		--nvidia-docker-binary-path)
			shift
			nvidia_docker_binary_path="${1}"
		;;
		--image-tag)
			shift
			image_tag="${1}"
		;;
		--)
			shift
			break
		;;
		*)
			echo "ERROR: unrecognized option: ${1}"
			exit 1
		;;
	esac
	shift
done

# check given arguments
if test -z "${docker_binary_path:-}"; then
    echo "docker-binary-path is unset! Override with --docker-binary-path"
    exit 1
elif test ! -f "${docker_binary_path}"; then
    echo "${docker_binary_path} not found! Override with --docker-binary-path"
    exit 1
elif test -z "${nvidia_docker_binary_path:-}"; then
    echo "nvidia-docker-binary-path is unset! Override with --nvidia-docker-binary-path"
    exit 1
elif test ! -f "${nvidia_docker_binary_path}"; then
    echo "${nvidia_docker_binary_path} not found! Override with --nvidia-docker-binary-path"
    exit 1
elif test -z "${docker_json_path:-}"; then
    echo "docker-json-path is unset! Override with --docker-json-path"
    exit 1
elif test ! -f "${docker_json_path}"; then
    echo "${docker_json_path} not found! Override with --docker-json-path"
    exit 1
elif test -z "${config_directory:-}"; then
    echo "config-directory is unset! Override with --config-directory"
    exit 1
elif test ! -d "${config_directory}"; then
    echo "${config_directory} not found! Override with --config-directory"
    exit 1
fi

# find latest release git hash if not set
if test -z "${image_tag:-}"; then
  image_tag="$(git -C "$(dirname "$(realpath "${0}")")" tag --list --sort=-creatordate --merged HEAD 'release/*' | head -n1 | cut -f2 -d/)"
  if test -z "${image_tag:-}"; then
    echo "finding image tag was failed"
    exit 1
  fi
fi

image_name="ultinous/uvap:kafka_passdet_${image_tag}"
container_name="uvap_kafka_passdet"

${docker_binary_path} pull ${image_name}
passdet_property_file_path="/ultinous_app/models/uvap-kafka-passdet/uvap_kafka_passdet.properties"

user_id="$(id -u)"
docker rm --force ${container_name} 2> /dev/null || true
# run image
${nvidia_docker_binary_path} run \
    --name ${container_name} \
    --detach \
    --user "${user_id}" \
    --mount "type=bind,readonly,source=$(realpath "${config_directory}"),destination=/ultinous_app/models/uvap-kafka-passdet/" \
    --env KAFKA_PASSDET_MS_PROPERTY_FILE_PATHS="${passdet_property_file_path}" \
    ${@} \
    ${image_name}
