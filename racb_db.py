from tinydb import TinyDB, Query
from datetime import datetime, timezone
import time

db = TinyDB('db.json', sort_keys=True, indent=4, separators=(',', ': '))


comments = db.table('comments', cache_size=0)
downvoted_crosspots = db.table('downvoted_crossposts', cache_size=0)

def add_comment(comment):
    dict = {
        'permalink': comment.permalink,
        #'scraped_itme_human_readable': datetime.now(timezone.utc).astimezone().isoformat(), 
        'scarped_time' : time.time(),
    }
    comments.insert(dict)



def get_comments_before_timedelta(timedelta):
    Comment = Query()
    return comments.search(Comment.scarped_time < time.time() - timedelta)
    
def remove_comment(comment_entry):
    comments.remove(doc_ids=[comment_entry.doc_id])
    

def add_downvoted_crosspost(submission):
    downvoted_crosspots.insert({
        'permalink': submission.permalink
    })