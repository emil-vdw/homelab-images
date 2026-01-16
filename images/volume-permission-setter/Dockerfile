# syntax=docker/dockerfile:1.7
FROM busybox:1.36

# Keep everything in one well-known place.
WORKDIR /app

# Copy the entrypoint script
COPY entrypoint.sh /app/entrypoint.sh

# Ensure it's executable
RUN chmod 0755 /app/entrypoint.sh

# Document required env vars
ENV TARGET="" \
    WANT_UID="" \
    WANT_GID="" \
    WANT_MODE=""

# Busybox default user is root; that's appropriate for chown/chmod in an init container.
ENTRYPOINT ["/app/entrypoint.sh"]
